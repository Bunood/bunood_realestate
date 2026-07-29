# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Financial Snapshot layer (plan-financial-reporting.md Phase 2) — compact live
figures INSIDE the form, so users are not forced into reports for a quick read.

Property already has its Finance page (property_finance) — NOT duplicated here.
This module serves the two remaining surfaces:
  * Lease Contract — invoiced / collected / outstanding (from the lease's own
    schedule-generated invoices), next due installment, deposit status.
  * Real Estate Unit — this-month revenue & expenses from the GL unit dimension,
    tenant arrears on the unit's invoices, current lease context.

Everything is read from native documents / GL — no parallel ledger, no caching
beyond the request (figures are cheap: all queries are indexed lookups).
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


def _lease_invoices(lease):
	"""The lease's Sales Invoices: rent + charge schedules + termination credits."""
	names = set()
	for dt in ("Rent Schedule", "Charge Schedule"):
		names.update(
			frappe.get_all(
				dt,
				filters={"lease_contract": lease, "sales_invoice": ["is", "set"]},
				pluck="sales_invoice",
			)
		)
	names.update(
		r[0]
		for r in frappe.db.sql(
			"""
			SELECT ltc.credit_note
			FROM `tabLease Termination Credit` ltc
			JOIN `tabLease Termination` lt ON lt.name = ltc.parent
			WHERE lt.lease_contract = %s AND ltc.credit_note IS NOT NULL
			""",
			lease,
		)
	)
	return {n for n in names if n}


@frappe.whitelist()
def lease_snapshot(lease):
	doc = frappe.get_doc("Lease Contract", lease)
	doc.check_permission("read")

	invoiced = outstanding = 0.0
	names = _lease_invoices(lease)
	if names:
		tup = tuple(names) if len(names) > 1 else (next(iter(names)),) * 2
		row = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(base_grand_total), 0), COALESCE(SUM(outstanding_amount), 0)
			FROM `tabSales Invoice` WHERE name IN %s AND docstatus = 1
			""",
			(tup,),
		)[0]
		invoiced, outstanding = flt(row[0]), flt(row[1])

	next_due = frappe.db.sql(
		"""
		SELECT due_date, base_amount FROM `tabRent Schedule`
		WHERE lease_contract = %(lease)s AND status = 'Planned' AND due_date >= %(today)s
		UNION ALL
		SELECT due_date, base_amount FROM `tabCharge Schedule`
		WHERE lease_contract = %(lease)s AND status = 'Planned' AND due_date >= %(today)s
		ORDER BY due_date ASC LIMIT 1
		""",
		{"lease": lease, "today": nowdate()},
		as_dict=True,
	)

	return {
		"invoiced": invoiced,
		"collected": flt(invoiced - outstanding),
		"outstanding": outstanding,
		"next_due_date": next_due[0].due_date if next_due else None,
		"next_due_amount": flt(next_due[0].base_amount) if next_due else 0.0,
		"deposit_amount": flt(doc.get("deposit_amount")),
		"deposit_received": bool(doc.get("deposit_received")),
	}


@frappe.whitelist()
def unit_snapshot(unit):
	u = frappe.get_doc("Real Estate Unit", unit)
	u.check_permission("read")

	month_start = getdate(nowdate()).replace(day=1)
	sums = frappe.db.sql(
		"""
		SELECT acc.root_type,
		       SUM(gle.credit) AS credit, SUM(gle.debit) AS debit
		FROM `tabGL Entry` gle
		JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE gle.is_cancelled = 0 AND gle.real_estate_unit = %(unit)s
		  AND gle.posting_date >= %(month_start)s
		  AND acc.root_type IN ('Income', 'Expense')
		GROUP BY acc.root_type
		""",
		{"unit": unit, "month_start": month_start},
		as_dict=True,
	)
	revenue = expense = 0.0
	for s in sums:
		if s.root_type == "Income":
			revenue = flt(s.credit) - flt(s.debit)
		elif s.root_type == "Expense":
			expense = flt(s.debit) - flt(s.credit)

	# Arrears: outstanding submitted invoices that carry a line for THIS unit.
	# The full invoice outstanding is reported (the receivable is one legal claim).
	arrears = flt(
		frappe.db.sql(
			"""
			SELECT COALESCE(SUM(si.outstanding_amount), 0)
			FROM `tabSales Invoice` si
			WHERE si.docstatus = 1 AND si.outstanding_amount > 0.005
			  AND EXISTS (
				SELECT 1 FROM `tabSales Invoice Item` sii
				WHERE sii.parent = si.name AND sii.real_estate_unit = %s
			  )
			""",
			unit,
		)[0][0]
	)

	lease = frappe.db.sql(
		"""
		SELECT lc.name, lc.customer, lc.end_date
		FROM `tabLease Contract` lc
		JOIN `tabLease Unit` lu ON lu.parent = lc.name
		WHERE lu.unit = %s AND lc.docstatus = 1 AND lc.status = 'Active'
		ORDER BY lc.start_date DESC LIMIT 1
		""",
		unit,
		as_dict=True,
	)

	return {
		"month_revenue": revenue,
		"month_expense": expense,
		"month_net": flt(revenue - expense),
		"arrears": arrears,
		"lease": lease[0].name if lease else None,
		"tenant": lease[0].customer if lease else None,
		"lease_end": lease[0].end_date if lease else None,
	}
