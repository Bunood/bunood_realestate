# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, add_months, date_diff, flt, getdate, nowdate

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


def build_escalated_periods(start_date, end_date, billing_cycle, annual_rent_total, escalation_pct=0):
	"""Escalating variant of :func:`build_periods` (pure, testable).

	The lease term is split into anniversary YEARS; year *n* (0-based) bills
	``annual_rent_total × (1 + pct/100)^n`` — the standard commercial step-up at each
	anniversary. Each year segment is generated through the VERIFIED ``build_periods``
	primitive, so every year keeps the exact-sum + cumulative-rounding + final-proration
	guarantees; period numbering continues across years. ``pct=0`` returns the identical
	output of plain ``build_periods`` (backward compatible)."""
	pct = flt(escalation_pct)
	if not pct:
		return build_periods(start_date, end_date, billing_cycle, annual_rent_total)
	start, end = getdate(start_date), getdate(end_date)
	factor = (100.0 + pct) / 100.0
	periods = []
	n = 0
	while True:
		seg_start = getdate(add_months(start, 12 * n))
		if seg_start > end:
			break
		# Mirror build_periods' boundary-artifact rule at SEGMENT level: an end date landing
		# exactly on an anniversary is covered by the prior year — without this, the 1-day
		# sliver becomes the new segment's exempt first period and bills a bogus 1-day invoice.
		if n > 0 and seg_start == end:
			break
		seg_end = min(getdate(add_days(add_months(start, 12 * (n + 1)), -1)), end)
		annual = flt(annual_rent_total) * (factor**n)
		segment = build_periods(seg_start, seg_end, billing_cycle, annual)
		# Keep year coverage CONTIGUOUS despite day-clamping (Feb-29 start → a non-leap
		# anniversary clamps to Feb-28 and re-anchoring build_periods there can drop the
		# segment's terminal day): extend the last period of a NON-final segment to seg_end.
		if segment and seg_end < end and getdate(segment[-1]["period_end"]) < seg_end:
			segment[-1]["period_end"] = seg_end
		for p in segment:
			p["period_no"] = len(periods) + 1
			periods.append(p)
		n += 1
	return periods


def escalation_segments(start_date, end_date):
	"""Pure: how many anniversary-year segments an escalated term spans (mirrors the
	build_escalated_periods loop, including its boundary-artifact rule). Used by renewal
	to roll rent forward to the FINAL escalated year before applying the renewal bump."""
	start, end = getdate(start_date), getdate(end_date)
	n = 0
	while True:
		seg_start = getdate(add_months(start, 12 * n))
		if seg_start > end or (n > 0 and seg_start == end):
			break
		n += 1
	return max(1, n)


def seed_future_periods(periods, cutoff):
	"""Pure/testable: for an imported (historical-seed) lease, drop periods already due before
	``cutoff`` — they are historical and must NOT become back-dated Sales Invoices (they are
	carried as an opening balance instead). ``cutoff=None`` (a normal, non-imported lease)
	keeps every period."""
	if not cutoff:
		return periods
	cut = getdate(cutoff)
	return [p for p in periods if getdate(p["period_start"]) >= cut]


def generate_for_lease(lease):
	"""Create Planned Rent Schedule rows for a submitted lease. Idempotent.

	Migration: a lease imported mid-term with ``import_historical_seed`` set bills only FUTURE
	periods (from today on); periods already due in the past are historical and are skipped
	here so activating an old contract never fires a batch of back-dated invoices."""
	if frappe.db.exists("Rent Schedule", {"lease_contract": lease.name}):
		return 0
	if not lease.units or not flt(lease.annual_rent_total):
		return 0

	periods = build_escalated_periods(
		lease.start_date, lease.end_date, lease.billing_cycle, lease.annual_rent_total,
		lease.get("escalation_pct"),
	)
	cutoff = nowdate() if lease.get("import_historical_seed") else None
	periods = seed_future_periods(periods, cutoff)
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
