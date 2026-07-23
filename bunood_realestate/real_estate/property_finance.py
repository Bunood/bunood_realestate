# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Per-property finance hub — income, expense and net for ONE property, entirely from
the ERPNext General Ledger via the Property accounting dimension.

Single source of truth = GL. We read `GL Entry` rows tagged with this property (the
dimension is copied onto every GL Entry by ERPNext) and split them by the account's
root type: Income = credit − debit, Expense = debit − credit. Net = income − expense.
Because the owner-payout expense is itself tagged with the property, a managed
property's net correctly collapses to the management fee — no parallel ledger, no
double counting. Company-scoped to what the caller may see.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, nowdate


def _assert_property_access(property):
	p = frappe.db.get_value(
		"Property", frappe.utils.cstr(property or ""), ["name", "company", "property_name"], as_dict=True
	)
	if not p:
		frappe.throw(_("Property not found."), frappe.DoesNotExistError)
	if p.company not in (frappe.get_list("Company", pluck="name") or []):
		frappe.throw(_("You do not have access to this property's company."), frappe.PermissionError)
	return p


@frappe.whitelist()
def property_finance(property, from_date=None, to_date=None):
	p = _assert_property_access(property)
	company = p.company

	conditions = ["gle.is_cancelled = 0", "gle.property = %(property)s", "gle.company = %(company)s"]
	values = {"property": p.name, "company": company}
	if from_date:
		conditions.append("gle.posting_date >= %(from_date)s")
		values["from_date"] = getdate(from_date)
	if to_date:
		conditions.append("gle.posting_date <= %(to_date)s")
		values["to_date"] = getdate(to_date)
	where = " AND ".join(conditions)

	rows = frappe.db.sql(
		f"""
		SELECT gle.account AS account, acc.root_type AS root_type,
		       SUM(gle.credit) AS credit, SUM(gle.debit) AS debit
		FROM `tabGL Entry` gle
		JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE {where}
		GROUP BY gle.account, acc.root_type
		ORDER BY acc.root_type, gle.account
		""",
		values,
		as_dict=True,
	)

	income, expense = [], []
	total_income = total_expense = 0.0
	for r in rows:
		if r.root_type == "Income":
			amt = flt(r.credit) - flt(r.debit)  # income is credit-normal
			if amt:
				income.append({"account": r.account, "amount": amt})
				total_income += amt
		elif r.root_type == "Expense":
			amt = flt(r.debit) - flt(r.credit)  # expense is debit-normal
			if amt:
				expense.append({"account": r.account, "amount": amt})
				total_expense += amt

	total_income = flt(total_income, 2)
	total_expense = flt(total_expense, 2)
	return {
		"property": p.name,
		"property_name": p.property_name,
		"company": company,
		"currency": frappe.get_cached_value("Company", company, "default_currency"),
		"income": income,
		"expense": expense,
		"total_income": total_income,
		"total_expense": total_expense,
		"net": flt(total_income - total_expense, 2),
		"monthly": _monthly(p.name, company),
		"occupancy": _occupancy(p.name),
	}


def _monthly(property, company):
	"""Income vs expense per month for the last 12 months, from GL, for this property."""
	start = getdate(add_months(nowdate(), -11)).replace(day=1)
	rows = frappe.db.sql(
		"""
		SELECT DATE_FORMAT(gle.posting_date, '%%Y-%%m') AS ym, acc.root_type AS root_type,
		       SUM(gle.credit) AS credit, SUM(gle.debit) AS debit
		FROM `tabGL Entry` gle
		JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE gle.is_cancelled = 0 AND gle.property = %(property)s AND gle.company = %(company)s
		  AND gle.posting_date >= %(start)s AND acc.root_type IN ('Income', 'Expense')
		GROUP BY ym, acc.root_type
		""",
		{"property": property, "company": company, "start": start},
		as_dict=True,
	)
	inc = {}
	exp = {}
	for r in rows:
		if r.root_type == "Income":
			inc[r.ym] = inc.get(r.ym, 0.0) + (flt(r.credit) - flt(r.debit))
		else:
			exp[r.ym] = exp.get(r.ym, 0.0) + (flt(r.debit) - flt(r.credit))
	labels, income_v, expense_v = [], [], []
	cur = start
	for _i in range(12):
		ym = cur.strftime("%Y-%m")
		labels.append(cur.strftime("%b %Y"))
		income_v.append(flt(inc.get(ym, 0.0), 2))
		expense_v.append(flt(exp.get(ym, 0.0), 2))
		cur = getdate(add_months(cur, 1))
	return {"labels": labels, "income": income_v, "expense": expense_v}


def _occupancy(property):
	"""Units held by a submitted Active lease vs total units of this property — the
	authoritative occupancy (never the mutable unit.status flag)."""
	total = flt(frappe.db.count("Real Estate Unit", {"property": property}))
	occupied = flt(
		frappe.db.sql(
			"""
			SELECT COUNT(DISTINCT lu.unit)
			FROM `tabLease Contract` lc
			JOIN `tabLease Unit` lu ON lu.parent = lc.name
			JOIN `tabReal Estate Unit` reu ON reu.name = lu.unit
			WHERE lc.docstatus = 1 AND lc.status = 'Active' AND reu.property = %s
			""",
			property,
		)[0][0]
	)
	return {
		"total": int(total),
		"occupied": int(occupied),
		"vacant": int(max(0.0, total - occupied)),
		"pct": round(occupied * 100.0 / total, 1) if total else 0.0,
	}
