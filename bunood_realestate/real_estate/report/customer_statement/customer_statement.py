# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Customer (tenant) statement of account.

Single source of truth = the ERPNext General Ledger. This report READS `GL Entry`
for the customer party and computes the running balance from it — it never keeps a
parallel balance (that was bunood_core's fatal mistake). Debit = the customer owes
(مدين), Credit = paid/credited (دائن), Balance = the real outstanding.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	customer = filters.get("customer")
	if not customer:
		return _columns(), []

	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), []

	values = {"customer": customer}
	conditions = ["gle.party_type = 'Customer'", "gle.party = %(customer)s", "gle.is_cancelled = 0"]

	company = filters.get("company")
	if company:
		if company not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("gle.company = %(company)s")
		values["company"] = company
	else:
		conditions.append("gle.company IN %(allowed)s")
		values["allowed"] = tuple(allowed) if len(allowed) > 1 else (allowed[0], allowed[0])

	base = " AND ".join(conditions)

	# Opening balance = everything strictly before the from-date.
	opening = 0.0
	from_date = filters.get("from_date")
	if from_date:
		values["from_date"] = from_date
		row = frappe.db.sql(
			f"SELECT COALESCE(SUM(gle.debit - gle.credit), 0) FROM `tabGL Entry` gle "
			f"WHERE {base} AND gle.posting_date < %(from_date)s",
			values,
		)
		opening = flt(row[0][0]) if row else 0.0

	period = list(conditions)
	if from_date:
		period.append("gle.posting_date >= %(from_date)s")
	to_date = filters.get("to_date")
	if to_date:
		period.append("gle.posting_date <= %(to_date)s")
		values["to_date"] = to_date

	rows = frappe.db.sql(
		f"""
		SELECT gle.posting_date, gle.voucher_type, gle.voucher_no,
		       gle.debit, gle.credit, gle.remarks
		FROM `tabGL Entry` gle
		WHERE {" AND ".join(period)}
		ORDER BY gle.posting_date ASC, gle.creation ASC
		""",
		values,
		as_dict=True,
	)

	data = []
	balance = opening
	if from_date:
		data.append({
			"posting_date": from_date, "voucher_no": _("Opening Balance"),
			"debit": 0, "credit": 0, "balance": opening,
		})
	for r in rows:
		balance += flt(r.debit) - flt(r.credit)
		r["balance"] = balance
		data.append(r)

	return _columns(), data


def _columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 130},
		{"label": _("Voucher"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 160},
		{"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 120},
		{"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 120},
		{"label": _("Balance"), "fieldname": "balance", "fieldtype": "Currency", "width": 130},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Small Text", "width": 240},
	]
