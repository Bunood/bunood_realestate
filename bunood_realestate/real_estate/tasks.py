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


def split_amount(base, weights):
	"""Split `base` across `weights` (per-unit annual rents); last line absorbs rounding.
	Pure & testable — keeps per-unit invoice lines summing exactly to the period rent."""
	base = flt(base)
	total = sum(flt(w) for w in weights)
	n = len(weights)
	shares = []
	running = 0.0
	for i, w in enumerate(weights):
		if i == n - 1:
			shares.append(flt(base - running, 2))
		else:
			s = flt(base * flt(w) / total, 2) if total else flt(base / n, 2)
			shares.append(s)
			running += s
	return shares


def generate_due_rent_invoices(lease_contract=None, lead_days=None):
	"""Scheduler entrypoint (daily). Idempotent, per-row transaction, fail-loud-per-row."""
	settings = frappe.get_single("Real Estate Settings")
	if lead_days is None:
		lead_days = int(settings.invoice_lead_days or 0)
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
			frappe.db.set_value(
				"Rent Schedule",
				name,
				{"status": "Failed", "invoice_status": str(e)[:140]},
				update_modified=False,
			)
			frappe.db.commit()
	return created


def _create_invoice_for_schedule(schedule_name, settings=None):
	settings = settings or frappe.get_single("Real Estate Settings")
	# Row lock: concurrent generators (scheduler + manual click) serialize here, so the
	# loser re-reads the committed state and the idempotency guard below stops it.
	frappe.db.get_value("Rent Schedule", schedule_name, "name", for_update=True)
	row = frappe.get_doc("Rent Schedule", schedule_name)

	# Idempotency guard — never double-invoice a period.
	if row.status != "Planned" or row.sales_invoice:
		return False
	if not settings.default_rent_item or not settings.rent_income_account:
		frappe.throw(_("Set Default Rent Item and Rent Income Account in Real Estate Settings."))
	# Single settings holds company-specific accounts → guard against wrong-company use
	# (one site = one company for now; see README "known limitations").
	if settings.company and row.company and settings.company != row.company:
		frappe.throw(
			_("Real Estate Settings is set for company {0} but this lease is for {1}; one site currently supports a single company.").format(
				settings.company, row.company
			)
		)

	lease = frappe.get_doc("Lease Contract", row.lease_contract)
	# Never invoice a lease that is not currently Active (cancelled / terminated / renewed).
	if lease.status != "Active":
		return False
	units = lease.units or []

	si = frappe.new_doc("Sales Invoice")
	si.customer = row.customer
	si.company = row.company
	si.set_posting_time = 1
	si.posting_date = row.due_date  # accrual: recognise revenue on the due date
	si.due_date = row.due_date
	if settings.receivable_account:
		si.debit_to = settings.receivable_account
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
				si, settings, share,
				unit=u.unit,
				property=lease.property or unit_property,
				period_start=row.period_start,
				period_end=row.period_end,
			)
	else:
		_append_rent_line(
			si, settings, row.base_amount,
			unit=None, property=lease.property,
			period_start=row.period_start, period_end=row.period_end,
		)

	# Parent-level Property dimension.
	si.property = lease.property

	# VAT by contract type: commercial 15% / residential exempt (Saudi ZATCA rule).
	# A Commercial lease MUST carry a tax template — otherwise we'd silently issue a
	# 0-VAT (ZATCA-non-compliant) invoice. Residential is legitimately exempt/untaxed.
	if lease.contract_type == "Commercial":
		template = settings.commercial_tax_template
		if not template:
			frappe.throw(
				_("Set a Commercial Tax Template in Real Estate Settings before invoicing a commercial lease (ZATCA requires 15% VAT).")
			)
	else:
		template = settings.residential_tax_template
	if template:
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		si.taxes_and_charges = template
		for tax in get_taxes_and_charges("Sales Taxes and Charges Template", template):
			si.append("taxes", tax)

	si.flags.ignore_permissions = True
	si.insert()
	if settings.auto_submit_invoices:
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
	if settings.default_cost_center:
		item.cost_center = settings.default_cost_center
	label = unit or _("Rent")
	item.description = _("Rent {0} ({1} to {2})").format(label, period_start, period_end)
	# Accounting dimensions (custom fields created by the app's fixtures).
	item.property = property
	if unit:
		item.real_estate_unit = unit


@frappe.whitelist()
def generate_now(lease_contract=None):
	"""Manual trigger (button). Same due-date rules as the scheduled job."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return generate_due_rent_invoices(lease_contract=lease_contract)
