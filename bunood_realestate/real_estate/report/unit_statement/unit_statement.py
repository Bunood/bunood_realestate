# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Unit Statement (كشف الوحدة) — every GL movement tagged with ONE unit.

Single source of truth = the ERPNext General Ledger via the Real Estate Unit
accounting dimension (rent invoice lines are unit-tagged by the generator; unit-level
maintenance/expense postings carry the dimension too). No parallel ledger.

Rows are classified by the account's root type: Income = credit − debit,
Expense = debit − credit; the summary shows Revenue / Expenses / Net for the window,
plus the unit's current lease context (tenant, contract, dates) as the message.
Company-scoped to the caller's permitted companies.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	unit = filters.get("unit")
	if not unit:
		return _columns(), []

	u = frappe.db.get_value(
		"Real Estate Unit", unit, ["name", "property", "status"], as_dict=True
	)
	if not u:
		frappe.throw(_("Unit not found."), frappe.DoesNotExistError)

	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), []

	conditions = ["gle.is_cancelled = 0", "gle.real_estate_unit = %(unit)s"]
	values = {"unit": unit}

	company = filters.get("company")
	if company:
		if company not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("gle.company = %(company)s")
		values["company"] = company
	else:
		conditions.append("gle.company IN %(allowed)s")
		values["allowed"] = tuple(allowed) if len(allowed) > 1 else (allowed[0], allowed[0])

	if filters.get("from_date"):
		conditions.append("gle.posting_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("gle.posting_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	rows = frappe.db.sql(
		f"""
		SELECT gle.posting_date, gle.voucher_type, gle.voucher_no,
		       gle.account, acc.root_type, gle.debit, gle.credit, gle.remarks,
		       pe.mode_of_payment AS payment_method
		FROM `tabGL Entry` gle
		JOIN `tabAccount` acc ON acc.name = gle.account
		LEFT JOIN `tabPayment Entry` pe
			ON gle.voucher_type = 'Payment Entry' AND pe.name = gle.voucher_no
		WHERE {" AND ".join(conditions)}
		ORDER BY gle.posting_date ASC, gle.creation ASC
		""",
		values,
		as_dict=True,
	)

	revenue = sum(flt(r.credit) - flt(r.debit) for r in rows if r.root_type == "Income")
	expense = sum(flt(r.debit) - flt(r.credit) for r in rows if r.root_type == "Expense")

	return (
		_columns(),
		rows,
		_lease_context(unit, u),
		None,
		_summary(revenue, expense),
	)


def _lease_context(unit, u):
	"""Current occupancy context from the AUTHORITATIVE source — the submitted Active
	lease holding this unit (not the mutable unit status flag)."""
	lease = frappe.db.sql(
		"""
		SELECT lc.name, lc.customer, lc.start_date, lc.end_date
		FROM `tabLease Contract` lc
		JOIN `tabLease Unit` lu ON lu.parent = lc.name
		WHERE lu.unit = %s AND lc.docstatus = 1 AND lc.status = 'Active'
		ORDER BY lc.start_date DESC LIMIT 1
		""",
		unit,
		as_dict=True,
	)
	if lease:
		l = lease[0]
		return _("Unit {0} — property {1} — leased to {2} ({3}, {4} to {5}).").format(
			unit, u.property, l.customer, l.name, l.start_date, l.end_date
		)
	return _("Unit {0} — property {1} — no active lease (status: {2}).").format(
		unit, u.property, u.status or _("Unknown")
	)


def _summary(revenue, expense):
	cur = frappe.defaults.get_global_default("currency") or ""
	net = flt(revenue - expense, 2)
	return [
		{"label": _("Revenue"), "value": flt(revenue, 2), "datatype": "Currency", "currency": cur, "indicator": "Blue"},
		{"label": _("Expenses"), "value": flt(expense, 2), "datatype": "Currency", "currency": cur, "indicator": "Orange"},
		{"label": _("Net"), "value": net, "datatype": "Currency", "currency": cur, "indicator": "Green" if net >= 0 else "Red"},
	]


def _columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 120},
		{"label": _("Voucher"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 160},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 190},
		{"label": _("Type"), "fieldname": "root_type", "fieldtype": "Data", "width": 90},
		{"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 120},
		{"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 120},
		{"label": _("Payment Method"), "fieldname": "payment_method", "fieldtype": "Link", "options": "Mode of Payment", "width": 125},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Small Text", "width": 220},
	]
