# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Phase 4 — accrual rent-invoice generation.

Turns due `Rent Schedule` rows (status Planned) into SUBMITTED (accrual) ERPNext
`Sales Invoice`s so the tenant shows as a debtor («معلّق») in the Statement of
Account until paid. All money lives in ERPNext — we never post GL ourselves.
Runs in the background (scheduler / manual button), never in a web request.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate

from bunood_realestate.real_estate import invoicing_policy
from bunood_realestate.real_estate.apportion import split_amount  # re-exported for back-compat
from bunood_realestate.real_estate.gl_utils import resolve_cost_center

__all__ = ["split_amount", "generate_due_rent_invoices", "generate_now"]


def generate_due_rent_invoices(lease_contract=None, lead_days=None, force=False):
	"""Scheduler entrypoint (daily). Idempotent, per-row transaction, fail-loud-per-row.

	Honors the site's Invoice Issuance Policy: under Manual / On Payment the daily job
	issues NOTHING (the Operations Center or «استلام الدفعة» does it on demand). An
	explicit operator action passes ``force=True`` — a human asking for the invoice is
	always allowed, whatever the automation policy says."""
	settings = frappe.get_single("Real Estate Settings")
	policy, policy_lead = invoicing_policy.current(settings)
	if not force and not invoicing_policy.auto_issues(policy):
		return 0
	if lead_days is None:
		lead_days = policy_lead
	cutoff = add_days(nowdate(), lead_days)

	filters = {
		"status": "Planned",
		"sales_invoice": ["in", [None, ""]],
		"due_date": ["<=", cutoff],
	}
	if lease_contract:
		filters["lease_contract"] = lease_contract

	names = frappe.get_all("Rent Schedule", filters=filters, order_by="due_date asc", pluck="name")
	created = 0
	for name in names:
		try:
			if _create_invoice_for_schedule(name, settings):
				created += 1
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: rent invoice generation failed",
				message=f"Rent Schedule {name}\n\n{frappe.get_traceback()}",
			)
			# Terminal, visible state so a persistently-failing row (e.g. a closed
			# accounting period) is not retried forever and silently un-invoiced.
			# An operator fixes the cause and resets the row to Planned.
			# Re-read UNDER A LOCK first: our exception may be a lost lock race whose
			# winner already invoiced this row, and stamping Failed over a live invoice
			# would strand it (the generator skips non-Planned rows forever).
			try:
				guard = frappe.db.get_value(
					"Rent Schedule", name, ["status", "sales_invoice"], for_update=True, as_dict=True
				)
				if guard and guard.status == "Planned" and not guard.sales_invoice:
					frappe.db.set_value(
						"Rent Schedule",
						name,
						{"status": "Failed", "invoice_status": str(e)[:140]},
						update_modified=False,
					)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(
					title="Bunood: could not record rent-invoice failure",
					message=f"Rent Schedule {name}\n\n{frappe.get_traceback()}",
				)
	return created


def _create_invoice_for_schedule(schedule_name, settings=None):
	settings = settings or frappe.get_single("Real Estate Settings")
	# Read the idempotency fields UNDER the row lock. A locking read always returns the
	# latest COMMITTED values, so the loser of a concurrent race (scheduler + manual
	# click) sees the committed sales_invoice and stops. Reading them via a plain
	# re-read after the lock could return a stale REPEATABLE-READ snapshot → double-invoice.
	guard = frappe.db.get_value(
		"Rent Schedule", schedule_name, ["status", "sales_invoice"], for_update=True, as_dict=True
	)
	if not guard or guard.status != "Planned" or guard.sales_invoice:
		return False
	row = frappe.get_doc("Rent Schedule", schedule_name)
	# Multi-company: resolve THIS document's company config through the single choke
	# point (profile → legacy Single fallback). Replaces the old per-field checks AND
	# the "one site = one company" hard-throw: a company with no profile and a
	# mismatched Single still fails loud, but with an actionable message.
	from bunood_realestate.real_estate.company_settings import require_company_config

	cfg = require_company_config(
		row.company, ["default_rent_item", "rent_income_account"], single=settings
	)

	lease = frappe.get_doc("Lease Contract", row.lease_contract)
	# Never invoice a lease that is not currently Active (cancelled / terminated / renewed).
	if lease.status != "Active":
		return False
	units = lease.units or []

	si = frappe.new_doc("Sales Invoice")
	si.customer = row.customer
	si.company = row.company
	# Pin to company currency — Rent Schedule base_amount is company-denominated (parity
	# with charge.py / head_lease.py). Without this, a customer whose default_currency
	# differs from the company would have the annual-rent slice mis-denominated/converted.
	si.currency = frappe.get_cached_value("Company", row.company, "default_currency")
	si.conversion_rate = 1
	si.set_posting_time = 1
	si.posting_date = row.due_date  # accrual: recognise revenue on the due date
	si.due_date = row.due_date
	if cfg.receivable_account:
		si.debit_to = cfg.receivable_account
	si.remarks = _("Rent for lease {0}, period {1} to {2}").format(
		lease.name, row.period_start, row.period_end
	)

	# One line per unit, each tagged with the Property + Unit accounting dimensions
	# → native per-property / per-unit P&L and ledgers.
	if units:
		weights = [flt(u.annual_rent) for u in units]
		shares = split_amount(row.base_amount, weights)
		for u, share in zip(units, shares):
			unit_property = frappe.db.get_value("Real Estate Unit", u.unit, "property")
			_append_rent_line(
				si, cfg, share,
				unit=u.unit,
				property=lease.property or unit_property,
				period_start=row.period_start,
				period_end=row.period_end,
			)
	else:
		_append_rent_line(
			si, cfg, row.base_amount,
			unit=None, property=lease.property,
			period_start=row.period_start, period_end=row.period_end,
		)

	# Parent-level Property dimension.
	si.property = lease.property

	# VAT by contract type: commercial 15% / residential exempt (Saudi ZATCA rule).
	# A Commercial lease MUST carry a tax template — otherwise we'd silently issue a
	# 0-VAT (ZATCA-non-compliant) invoice. Residential is legitimately exempt/untaxed.
	if lease.contract_type == "Commercial":
		template = cfg.commercial_tax_template
		if not template:
			frappe.throw(
				_("Set a Commercial Tax Template for company {0} before invoicing a commercial lease (ZATCA requires 15% VAT).").format(row.company)
			)
	else:
		template = cfg.residential_tax_template
	if template:
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		si.taxes_and_charges = template
		for tax in get_taxes_and_charges("Sales Taxes and Charges Template", template):
			si.append("taxes", tax)

	si.flags.ignore_permissions = True
	si.insert()
	# Issuance always submits: a draft reaches neither the GL nor ZATCA, so "issued but
	# draft" has no business meaning. WHEN to issue is the Invoice Issuance Policy's job.
	si.submit()  # becomes «معلّق» (Unpaid) → shows on the tenant Statement of Account

	frappe.db.set_value(
		"Rent Schedule",
		row.name,
		{"sales_invoice": si.name, "status": "Invoiced", "invoice_status": si.status},
	)
	return True


def _append_rent_line(si, settings, rate, unit, property, period_start, period_end):
	item = si.append("items", {})
	item.item_code = settings.default_rent_item
	item.qty = 1
	item.rate = flt(rate)
	item.income_account = settings.rent_income_account
	# Use a cost center that belongs to THIS invoice's company (a settings default set
	# for another company would be rejected). If none, ERPNext fills the company default.
	cost_center = resolve_cost_center(si.company)
	if cost_center:
		item.cost_center = cost_center
	label = unit or _("Rent")
	item.description = _("Rent {0} ({1} to {2})").format(label, period_start, period_end)
	# Accounting dimensions (custom fields created by the app's fixtures).
	item.property = property
	if unit:
		item.real_estate_unit = unit


@frappe.whitelist()
def generate_now(lease_contract=None):
	"""Manual trigger (button). Same due-date rules as the scheduled job, but an explicit
	human request overrides a non-auto policy (force=True)."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return generate_due_rent_invoices(lease_contract=lease_contract, force=True)
