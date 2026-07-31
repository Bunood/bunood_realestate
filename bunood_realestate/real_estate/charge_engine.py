# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Charge Engine — recurring utilities/services billing, INDEPENDENT of rent.

Mirrors the verified Rent pattern byte-for-byte: `build_periods` → Planned `Charge Schedule`
rows → a daily row-locked generator that posts native ERPNext Sales Invoices. It touches
NEITHER `tasks.py` (rent) NOR the shared `core/charge.py` poster — its own generator keeps the
verified money paths untouched. Grouping is driven by the lease's `Billing Policy` behavior; every
bucket additionally sub-partitions by (customer, company, tax_template, due_date) so each invoice
is tax-homogeneous (ZATCA-correct) by construction.

Rent stays first-class on its own rail; this engine handles everything else.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import (
	INSTALLMENTS_PER_YEAR,
	build_periods,
	seed_future_periods,
)
from bunood_realestate.real_estate.gl_utils import resolve_cost_center


# --------------------------------------------------------------------------------------
# Pure / testable helpers
# --------------------------------------------------------------------------------------
def charge_due_date(period_start, period_end, timing):
	"""Advance charges fall due at the period START (like rent); Arrears at the period END
	(utilities — you bill after the service period; metered is always Arrears)."""
	return getdate(period_end) if timing == "Arrears" else getdate(period_start)


def group_key(row, behavior):
	"""The invoice-bucket key for a due Charge Schedule row under a Billing Policy behavior.

	EVERY behavior sub-partitions by (customer, company, tax_template, due_date) so no invoice
	ever mixes tax templates or due dates. The behavior only decides how much MORE to split:
	  - separate           → also per charge line  (one invoice per charge)
	  - group_by_category  → also per Charge Type kind
	  - single             → nothing more (one invoice for all the tenant's due charges)
	"""
	base = (row.get("customer"), row.get("company"), row.get("tax_template"), str(row.get("due_date")))
	if behavior == "single":
		return base
	if behavior == "group_by_category":
		return base + (row.get("category"),)
	return base + (row.get("lease_charge_row"),)  # 'separate' (default)


def partition(rows, behavior):
	"""Group rows into invoice buckets by group_key. Pure; deterministic order preserved."""
	buckets = {}
	for r in rows:
		buckets.setdefault(group_key(r, behavior), []).append(r)
	return list(buckets.values())


def compute_consumption(previous, current, meter_replaced=False, replaced_meter_final=0.0):
	"""Metered consumption. Normally current-previous. On a replaced/rolled-over meter the new
	meter restarts near 0, so consumption = current + the OLD meter's final reading - previous.
	Raises on a genuine negative (a mis-keyed reading) so bad data can't post a negative bill."""
	previous, current = flt(previous), flt(current)
	if meter_replaced:
		consumption = flt(current) + flt(replaced_meter_final) - previous
	else:
		consumption = current - previous
	if consumption < 0:
		frappe.throw(
			_("Current reading {0} is below the previous reading {1}. If the meter was replaced or rolled over, tick 'Meter Replaced'.").format(
				current, previous
			)
		)
	return consumption


# --------------------------------------------------------------------------------------
# Phase 2 — seeder (mirror rent_schedule.generate_for_lease)
# --------------------------------------------------------------------------------------
def _effective_range(lease, charge):
	"""Clip the charge's own [charge_start, charge_end] to the lease term."""
	start = getdate(charge.get("charge_start_date") or lease.start_date)
	end = getdate(charge.get("charge_end_date") or lease.end_date)
	start = max(start, getdate(lease.start_date))
	end = min(end, getdate(lease.end_date))
	return start, end


def _resolve_tax_template(lease, charge, settings):
	if charge.get("tax_template"):
		return charge.get("tax_template")
	if lease.get("contract_type") == "Commercial":
		return settings.get("commercial_tax_template")
	return settings.get("residential_tax_template")


def seed_charges_for_lease(lease, settings=None, cutoff=None):
	"""Create Planned (Fixed) / Awaiting-Reading (Metered) Charge Schedule rows for every active
	Lease Charge. Idempotent per (lease_charge_row, period_no). Honors import_historical_seed like
	rent (an imported mid-term lease bills only future periods). An explicit ``cutoff`` overrides
	that (the migration passes today so a mid-term lease is never back-billed for utilities)."""
	from bunood_realestate.real_estate.company_settings import get_company_config

	settings = settings or frappe.get_single("Real Estate Settings")
	# Multi-company: seed with the LEASE company's resolved config (nullable — seeding
	# isn't posting, and the generator re-resolves live at invoice time anyway).
	cfg = get_company_config(lease.company, single=settings) or frappe._dict()
	if cutoff is None:
		cutoff = nowdate() if lease.get("import_historical_seed") else None
	created = 0
	for charge in (lease.get("charges") or []):
		if not charge.get("is_active") or not charge.get("charge_type"):
			continue
		cycle = charge.get("billing_cycle") or lease.billing_cycle
		metered = charge.get("billing_method") == "Metered"
		# Fixed: per-period amount → annual-equivalent so build_periods yields it per period.
		annual_equiv = 0.0 if metered else flt(charge.get("amount")) * INSTALLMENTS_PER_YEAR[cycle]
		start, end = _effective_range(lease, charge)
		if start > end:
			continue
		periods = seed_future_periods(build_periods(start, end, cycle, annual_equiv), cutoff)
		timing = "Arrears" if metered else (charge.get("billing_timing") or "Arrears")
		tax_template = _resolve_tax_template(lease, charge, cfg)
		unit = charge.get("unit")
		unit_property = frappe.db.get_value("Real Estate Unit", unit, "property") if unit else None
		for p in periods:
			if frappe.db.exists(
				"Charge Schedule",
				{"lease_charge_row": charge.get("name"), "period_no": p["period_no"]},
			):
				continue
			frappe.get_doc({
				"doctype": "Charge Schedule",
				"lease_contract": lease.name,
				"lease_charge_row": charge.get("name"),
				"charge_type": charge.get("charge_type"),
				"category": frappe.db.get_value("Charge Type", charge.get("charge_type"), "charge_kind"),
				"billing_method": charge.get("billing_method") or "Fixed",
				"customer": lease.customer,
				"property": lease.property or unit_property,
				"unit": unit,
				"company": lease.company,
				"period_no": p["period_no"],
				"period_start": p["period_start"],
				"period_end": p["period_end"],
				"due_date": charge_due_date(p["period_start"], p["period_end"], timing),
				"billing_cycle": cycle,
				"base_amount": p["base_amount"],
				"is_prorated": p["is_prorated"],
				"revenue_account": charge.get("revenue_account"),
				"tax_template": tax_template,
				"meter_no": charge.get("meter_no"),
				"previous_reading": charge.get("previous_reading") if metered else None,
				"tariff": charge.get("tariff") if metered else None,
				"status": "Awaiting Reading" if metered else "Planned",
			}).insert(ignore_permissions=True)
			created += 1
	return created


def cancel_charges_for_lease(lease):
	"""On lease cancel: delete still-open (un-invoiced) rows, mark invoiced ones Cancelled."""
	rows = frappe.get_all(
		"Charge Schedule",
		filters={"lease_contract": lease.name},
		fields=["name", "status", "sales_invoice"],
	)
	for r in rows:
		if r.status in ("Planned", "Awaiting Reading") and not r.sales_invoice:
			frappe.delete_doc("Charge Schedule", r.name, ignore_permissions=True, force=True)
		elif r.status != "Invoiced":
			frappe.db.set_value("Charge Schedule", r.name, "status", "Cancelled")


def cancel_future_charges(lease_contract, from_date):
	"""On termination: cancel still-open charge rows due on/after the termination date
	(mirror of LeaseTermination._cancel_future_rent)."""
	rows = frappe.get_all(
		"Charge Schedule",
		filters={
			"lease_contract": lease_contract,
			"status": ["in", ["Planned", "Awaiting Reading"]],
			"due_date": [">=", getdate(from_date)],
			"sales_invoice": ["in", [None, ""]],
		},
		pluck="name",
	)
	for name in rows:
		frappe.db.set_value("Charge Schedule", name, "status", "Cancelled")
	return len(rows)


def restore_future_charges(lease_contract, from_date):
	"""Reactivating a terminated lease: re-open the charge rows this termination cancelled.
	A metered row with no captured reading returns to Awaiting Reading; everything else to Planned."""
	rows = frappe.get_all(
		"Charge Schedule",
		filters={
			"lease_contract": lease_contract,
			"status": "Cancelled",
			"sales_invoice": ["in", [None, ""]],
			"due_date": [">=", getdate(from_date)],
		},
		fields=["name", "billing_method", "base_amount"],
	)
	for r in rows:
		status = "Awaiting Reading" if (r.billing_method == "Metered" and not flt(r.base_amount)) else "Planned"
		frappe.db.set_value("Charge Schedule", r.name, "status", status)
	return len(rows)


# --------------------------------------------------------------------------------------
# Phase 3 — generator + Billing Policy grouping (mirror tasks.generate_due_rent_invoices)
# --------------------------------------------------------------------------------------
def generate_due_charge_invoices(lease_contract=None, lead_days=None):
	"""Scheduler entrypoint (daily). Idempotent, per-bucket transaction, fail-loud-per-bucket."""
	settings = frappe.get_single("Real Estate Settings")
	if lead_days is None:
		lead_days = int(settings.invoice_lead_days or 0)
	cutoff = add_days(nowdate(), lead_days)

	filters = {"status": "Planned", "sales_invoice": ["in", [None, ""]], "due_date": ["<=", cutoff]}
	if lease_contract:
		filters["lease_contract"] = lease_contract
	rows = frappe.get_all(
		"Charge Schedule",
		filters=filters,
		fields=[
			"name", "lease_contract", "lease_charge_row", "charge_type", "category",
			"customer", "company", "property", "unit", "base_amount", "revenue_account",
			"tax_template", "due_date", "period_start", "period_end",
		],
		order_by="lease_contract asc, due_date asc",
	)
	# Split per lease (billing policy is per-lease), then partition each lease's rows into buckets.
	by_lease = {}
	for r in rows:
		by_lease.setdefault(r["lease_contract"], []).append(r)

	created = 0
	for lease_name, lease_rows in by_lease.items():
		behavior = _lease_policy_behavior(lease_name)
		for bucket in partition(lease_rows, behavior):
			try:
				if _create_charge_invoice_for_bucket(bucket, settings):
					created += 1
				frappe.db.commit()
			except Exception as e:
				frappe.db.rollback()
				frappe.log_error(
					title="Bunood: charge invoice generation failed",
					message=f"Charge Schedule bucket {[r['name'] for r in bucket]}\n\n{frappe.get_traceback()}",
				)
				# Mark Failed ONLY rows that are still genuinely un-invoiced. A row in this
				# bucket may have been invoiced by a concurrent run (excluded from `live`
				# under the lock) — overwriting IT to Failed would corrupt a correct period
				# and invite a double-invoice on the documented Failed-recovery path.
				for r in bucket:
					guard = frappe.db.get_value(
						"Charge Schedule", r["name"], ["status", "sales_invoice"], as_dict=True
					)
					if guard and guard.status == "Planned" and not guard.sales_invoice:
						frappe.db.set_value(
							"Charge Schedule", r["name"],
							{"status": "Failed", "invoice_status": str(e)[:140]}, update_modified=False,
						)
				frappe.db.commit()
	return created


def _lease_policy_behavior(lease_name):
	policy = frappe.db.get_value("Lease Contract", lease_name, "billing_policy")
	behavior = frappe.db.get_value("Billing Policy", policy, "behavior") if policy else None
	return behavior or "separate"


def _create_charge_invoice_for_bucket(bucket, settings):
	"""Lock each row, re-check idempotency under the lock, then post ONE native Sales Invoice
	for the bucket. A row already taken by a concurrent run is skipped (never double-invoiced)."""
	live = []
	for r in bucket:
		guard = frappe.db.get_value(
			"Charge Schedule", r["name"], ["status", "sales_invoice"], for_update=True, as_dict=True
		)
		if guard and guard.status == "Planned" and not guard.sales_invoice:
			live.append(r)
	if not live:
		return False

	first = live[0]
	# Never invoice a lease that is not currently Active.
	lease_info = frappe.db.get_value(
		"Lease Contract", first["lease_contract"], ["status", "contract_type"], as_dict=True
	)
	if not lease_info or lease_info.status != "Active":
		return False
	# Multi-company: resolve THIS bucket's company config (profile → legacy Single);
	# fails loud for an unconfigured company — the successor of the old mismatch throw.
	from bunood_realestate.real_estate.company_settings import (
		all_configured_values,
		require_company_config,
	)

	cfg = require_company_config(first["company"], single=settings)

	si = frappe.new_doc("Sales Invoice")
	si.customer = first["customer"]
	si.company = first["company"]
	si.currency = frappe.get_cached_value("Company", first["company"], "default_currency")
	si.conversion_rate = 1
	si.set_posting_time = 1
	si.posting_date = first["due_date"]
	si.due_date = first["due_date"]
	if cfg.receivable_account:
		si.debit_to = cfg.receivable_account
	si.remarks = _("Charges for lease {0}").format(first["lease_contract"])

	cost_center = resolve_cost_center(si.company)
	for r in live:
		item_code = frappe.db.get_value("Charge Type", r["charge_type"], "item")
		if not item_code:
			frappe.throw(_("Set a Service Item on Charge Type {0} before billing it.").format(r["charge_type"]))
		# The rent Service Item is the POSITIVE discriminator the cash-basis owner payout
		# uses to tell rent cash from charge cash. A charge line must therefore never use
		# it — otherwise its cash would be folded into the owner's rent base (over-pay).
		if item_code in all_configured_values("default_rent_item"):
			frappe.throw(
				_("Charge Type {0} uses a Rent Service Item ({1}). Charges must carry their own Service Item so charge cash is never counted as rent for the owner payout.").format(
					r["charge_type"], item_code
				)
			)
		item = si.append("items", {})
		item.item_code = item_code
		item.qty = 1
		item.rate = flt(r["base_amount"])
		if r.get("revenue_account"):
			item.income_account = r["revenue_account"]
		if cost_center:
			item.cost_center = cost_center
		item.property = r.get("property")
		if r.get("unit"):
			item.real_estate_unit = r["unit"]
		item.description = _("{0} ({1} to {2})").format(
			r["charge_type"], r["period_start"], r["period_end"]
		)

	tax_template = first.get("tax_template")
	if not tax_template:
		# Re-resolve LIVE (mirror of the rent path): a row seeded before the tax templates
		# were configured must not stay 0-VAT forever — later configuration is honored.
		tax_template = (
			cfg.commercial_tax_template
			if lease_info.contract_type == "Commercial"
			else cfg.residential_tax_template
		)
	# A Commercial charge MUST carry a tax template — otherwise we'd silently issue a
	# 0-VAT (ZATCA-non-compliant) invoice. Residential is legitimately exempt/untaxed.
	if lease_info.contract_type == "Commercial" and not tax_template:
		frappe.throw(
			_("Set a Commercial Tax Template for company {0} before billing charges on a commercial lease (ZATCA requires 15% VAT).").format(first["company"])
		)
	if tax_template:
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		si.taxes_and_charges = tax_template
		for tax in get_taxes_and_charges("Sales Taxes and Charges Template", tax_template):
			si.append("taxes", tax)

	si.flags.ignore_permissions = True
	si.insert()
	if cfg.auto_submit_invoices:
		si.submit()

	for r in live:
		frappe.db.set_value(
			"Charge Schedule", r["name"],
			{"sales_invoice": si.name, "status": "Invoiced", "invoice_status": si.status},
		)
	return True


@frappe.whitelist()
def generate_charges_now(lease_contract=None):
	"""Manual trigger (button). Same due-date rules as the scheduled job."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return generate_due_charge_invoices(lease_contract=lease_contract)


# --------------------------------------------------------------------------------------
# Phase 4 — metered capture (called from Meter Reading on_submit / on_cancel)
# --------------------------------------------------------------------------------------
def capture_meter_reading(reading):
	"""Meter Reading on_submit: compute consumption, fill the target Awaiting-Reading Charge
	Schedule row (base_amount = consumption × tariff → Planned) and advance the charge's rolling
	previous_reading. The billed period is then invoiced by the normal generator.

	Concurrency: the target row is re-checked UNDER a for_update lock (mirror of the invoice
	generator) so two concurrent readings can never clobber the same period — the loser sees the
	committed 'Planned' status and fails loudly instead of silently overwriting."""
	target = _reading_target_row(reading)
	if not target:
		frappe.throw(_("No open (Awaiting Reading) charge period found for this meter/charge."))
	# Lock + re-check under the lock: a plain read could see a stale snapshot and double-fill.
	guard = frappe.db.get_value(
		"Charge Schedule", target, ["status", "sales_invoice"], for_update=True, as_dict=True
	)
	if not guard or guard.status != "Awaiting Reading" or guard.sales_invoice:
		frappe.throw(
			_("Charge period {0} is no longer awaiting a reading (already captured or invoiced). Refresh and pick the correct period.").format(target)
		)
	# Persist the resolved target ON the reading — on_cancel needs it to revert an
	# auto-targeted capture (else cancelling a wrong reading would be a silent no-op).
	if not reading.charge_schedule:
		reading.db_set("charge_schedule", target)

	row = frappe.get_doc("Charge Schedule", target)
	# Baseline priority: an explicitly entered previous_reading wins; else the Lease Charge's
	# ROLLING previous_reading (advanced by every capture — the true prior-period baseline);
	# else the row's seed-time snapshot. Without the rolling source, period 2+ would re-use the
	# stale seed baseline and over-bill every later period.
	if reading.previous_reading:
		previous = flt(reading.previous_reading)
	else:
		rolling = (
			frappe.db.get_value("Lease Charge", reading.lease_charge_row, "previous_reading")
			if reading.lease_charge_row
			else None
		)
		previous = flt(rolling) if rolling is not None else flt(row.previous_reading)
	consumption = compute_consumption(
		previous, reading.current_reading, reading.meter_replaced, reading.replaced_meter_final
	)
	tariff = flt(row.tariff)
	reading.db_set("previous_reading", previous)
	reading.db_set("consumption", consumption)
	row.db_set("previous_reading", previous)
	row.db_set("current_reading", flt(reading.current_reading))
	row.db_set("consumption", consumption)
	row.db_set("bill", flt(consumption * tariff, 2))
	row.db_set("base_amount", flt(consumption * tariff, 2))
	row.db_set("status", "Planned")
	# Advance the Lease Charge's rolling reading so the next period starts from here.
	if reading.lease_charge_row:
		frappe.db.set_value("Lease Charge", reading.lease_charge_row, "previous_reading", flt(reading.current_reading))


def revert_meter_reading(reading):
	"""Meter Reading on_cancel: put the filled period back to Awaiting Reading if it is not yet
	invoiced, so a corrected reading can be captured. Guard is read UNDER a for_update lock —
	a concurrent generator run may be invoicing this row right now; the loser of that race must
	see the committed invoice and skip, never clobber an invoiced period back to zero."""
	if not reading.charge_schedule:
		return
	row = frappe.db.get_value(
		"Charge Schedule", reading.charge_schedule, ["status", "sales_invoice"],
		for_update=True, as_dict=True,
	)
	if row and row.status == "Planned" and not row.sales_invoice:
		frappe.db.set_value(
			"Charge Schedule", reading.charge_schedule,
			{"status": "Awaiting Reading", "base_amount": 0, "current_reading": 0, "consumption": 0, "bill": 0},
			update_modified=False,
		)


def _reading_target_row(reading):
	if reading.charge_schedule:
		return reading.charge_schedule
	# Else the earliest open metered period for this charge.
	rows = frappe.get_all(
		"Charge Schedule",
		filters={"lease_charge_row": reading.lease_charge_row, "status": "Awaiting Reading"},
		order_by="due_date asc", limit=1, pluck="name",
	)
	return rows[0] if rows else None


# --------------------------------------------------------------------------------------
# Phase 5 — lifecycle: reset a charge row when its Sales Invoice is cancelled
# --------------------------------------------------------------------------------------
def reset_charge_schedule_on_invoice(doc, method=None):
	"""Sales Invoice cancel/trash doc_event: free the charge period(s) to be re-invoiced
	(mirror of rent's _revert_schedule_rows). Metered rows keep their captured reading, so they
	return to Planned (billable), not Awaiting Reading."""
	rows = frappe.get_all("Charge Schedule", filters={"sales_invoice": doc.name}, pluck="name")
	for name in rows:
		frappe.db.set_value(
			"Charge Schedule", name,
			{"sales_invoice": None, "status": "Planned", "invoice_status": None},
			update_modified=False,
		)
