# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, add_months, date_diff, flt, getdate

# Billing cycle -> installments per year and months covered per period.
INSTALLMENTS_PER_YEAR = {"Monthly": 12, "Quarterly": 4, "Semi-Annual": 2, "Annual": 1}
CYCLE_MONTHS = {"Monthly": 1, "Quarterly": 3, "Semi-Annual": 6, "Annual": 12}


class RentSchedule(Document):
	pass


def build_periods(start_date, end_date, billing_cycle, annual_rent_total):
	"""Pure, deterministic schedule generator (no DB writes — easy to test).

	Rules (parity with bunood_core, improved with final-period proration):
	  - due_date = period_start.
	  - Periods step by the cycle's months from start_date (anniversary-day billing),
	    counted while period_start <= end_date (half-open coverage; end_date inclusive).
	  - Full installment = annual_rent_total / installments_per_year.
	  - The final period is clamped to end_date and prorated by actual/again-full days.
	Returns a list of dicts: period_no, period_start, period_end, base_amount, is_prorated.
	"""
	start = getdate(start_date)
	end = getdate(end_date)
	months = CYCLE_MONTHS[billing_cycle]
	per_year = INSTALLMENTS_PER_YEAR[billing_cycle]
	full_installment = flt(annual_rent_total) / per_year

	periods = []
	n = 0
	unrounded_total = 0.0
	rounded_total = 0.0
	while True:
		# Anchor every period to the ORIGINAL start (add_months from start, not from the
		# previous cursor) so month-end / Feb-29 day-clamping never drifts or accumulates.
		p_start = getdate(add_months(start, n * months))
		if p_start > end:
			break
		# A period whose start lands exactly on end_date (after clamping) is a boundary
		# artifact, not a real period — the prior full period already covers the term.
		if n > 0 and p_start == end:
			break

		natural_next = getdate(add_months(start, (n + 1) * months))
		natural_end = add_days(natural_next, -1)
		period_end = min(natural_end, end)

		full_days = date_diff(natural_end, p_start) + 1
		actual_days = date_diff(period_end, p_start) + 1
		is_prorated = actual_days != full_days

		# Cumulative rounding: the running rounded total tracks the running exact total,
		# so the periods sum exactly to the (2dp) annual rent — no per-period drift.
		exact = full_installment * actual_days / full_days if is_prorated else full_installment
		unrounded_total += exact
		amount = flt(flt(unrounded_total, 2) - rounded_total, 2)
		rounded_total = flt(rounded_total + amount, 2)

		n += 1
		periods.append(
			{
				"period_no": n,
				"period_start": p_start,
				"period_end": period_end,
				"base_amount": amount,
				"is_prorated": 1 if is_prorated else 0,
			}
		)

	return periods


def generate_for_lease(lease):
	"""Create Planned Rent Schedule rows for a submitted lease. Idempotent."""
	if frappe.db.exists("Rent Schedule", {"lease_contract": lease.name}):
		return 0
	if not lease.units or not flt(lease.annual_rent_total):
		return 0

	periods = build_periods(
		lease.start_date, lease.end_date, lease.billing_cycle, lease.annual_rent_total
	)
	for p in periods:
		frappe.get_doc(
			{
				"doctype": "Rent Schedule",
				"lease_contract": lease.name,
				"customer": lease.customer,
				"property": lease.property,
				"company": lease.company,
				"period_no": p["period_no"],
				"period_start": p["period_start"],
				"period_end": p["period_end"],
				"due_date": p["period_start"],
				"base_amount": p["base_amount"],
				"is_prorated": p["is_prorated"],
				"status": "Planned",
			}
		).insert(ignore_permissions=True)
	return len(periods)


def cancel_for_lease(lease):
	"""On lease cancel: delete still-Planned rows, mark any already-invoiced ones Cancelled."""
	rows = frappe.get_all(
		"Rent Schedule",
		filters={"lease_contract": lease.name},
		fields=["name", "status", "sales_invoice"],
	)
	for r in rows:
		if r.status == "Planned" and not r.sales_invoice:
			frappe.delete_doc("Rent Schedule", r.name, ignore_permissions=True, force=True)
		else:
			frappe.db.set_value("Rent Schedule", r.name, "status", "Cancelled")
