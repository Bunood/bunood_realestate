# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Owner statement — what a managed-property owner was actually paid.

Owner-facing counterpart to the tenant Customer Statement. It lists the REAL posted
`Owner Payout` records (rent base, management fee, net payout) for the owner's
properties in the period — no parallel figures, no accounting-policy guesses: every
number is a business document the company already committed to. Company-scoped by the
user's permitted companies; an explicit company filter is validated against them.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	owner = filters.get("owner")
	property = filters.get("property")
	if not owner and not property:
		return _columns(), [], None, None, _report_summary(0, 0, 0)

	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), [], None, None, _report_summary(0, 0, 0)

	conditions = ["op.status = 'Posted'"]
	values = {}
	if owner:
		conditions.append("op.owner_party = %(owner)s")
		values["owner"] = owner
	if property:
		conditions.append("op.property = %(property)s")
		values["property"] = property

	company = filters.get("company")
	if company:
		if company not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("op.company = %(company)s")
		values["company"] = company
	else:
		conditions.append("op.company IN %(allowed)s")
		values["allowed"] = tuple(allowed) if len(allowed) > 1 else (allowed[0], allowed[0])

	from_date = filters.get("from_date")
	if from_date:
		conditions.append("op.to_date >= %(from_date)s")
		values["from_date"] = from_date
	to_date = filters.get("to_date")
	if to_date:
		conditions.append("op.from_date <= %(to_date)s")
		values["to_date"] = to_date

	rows = frappe.db.sql(
		f"""
		SELECT op.property, op.owner_party, op.from_date, op.to_date,
		       op.rent_base, op.fee_percentage, op.fee_amount, op.owner_payout,
		       op.journal_entry
		FROM `tabOwner Payout` op
		WHERE {" AND ".join(conditions)}
		ORDER BY op.property ASC, op.from_date ASC
		""",
		values,
		as_dict=True,
	)

	total_rent = sum(flt(r.rent_base) for r in rows)
	total_fee = sum(flt(r.fee_amount) for r in rows)
	total_payout = sum(flt(r.owner_payout) for r in rows)
	return _columns(), rows, None, None, _report_summary(total_rent, total_fee, total_payout)


def _report_summary(rent, fee, payout):
	cur = frappe.defaults.get_global_default("currency") or ""
	return [
		{"label": _("Rent Base"), "value": flt(rent, 2), "datatype": "Currency", "currency": cur, "indicator": "Blue"},
		{"label": _("Management Fee"), "value": flt(fee, 2), "datatype": "Currency", "currency": cur, "indicator": "Orange"},
		{"label": _("Net Paid to Owner"), "value": flt(payout, 2), "datatype": "Currency", "currency": cur, "indicator": "Green"},
	]


def _columns():
	return [
		{"label": _("Property"), "fieldname": "property", "fieldtype": "Link", "options": "Property", "width": 180},
		{"label": _("Owner"), "fieldname": "owner_party", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("From"), "fieldname": "from_date", "fieldtype": "Date", "width": 100},
		{"label": _("To"), "fieldname": "to_date", "fieldtype": "Date", "width": 100},
		{"label": _("Rent Base"), "fieldname": "rent_base", "fieldtype": "Currency", "width": 120},
		{"label": _("Fee %"), "fieldname": "fee_percentage", "fieldtype": "Percent", "width": 80},
		{"label": _("Management Fee"), "fieldname": "fee_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Net Payout"), "fieldname": "owner_payout", "fieldtype": "Currency", "width": 140},
		{"label": _("Journal Entry"), "fieldname": "journal_entry", "fieldtype": "Link", "options": "Journal Entry", "width": 150},
	]
