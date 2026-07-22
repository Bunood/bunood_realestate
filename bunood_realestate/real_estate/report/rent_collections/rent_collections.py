# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Rent Collections / Dues — outstanding rent per invoiced period, with aging.
Reads native Sales Invoice outstanding (ERPNext is the source of truth)."""

import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	conditions = ["rs.status = 'Invoiced'", "si.docstatus = 1"]
	values = {}

	# Enforce company User Permissions: Script Report SQL is not auto-scoped.
	allowed = frappe.get_list("Company", pluck="name")
	if filters.get("company"):
		if filters["company"] not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("rs.company = %(company)s")
		values["company"] = filters["company"]
	else:
		conditions.append("rs.company IN %(allowed_companies)s")
		values["allowed_companies"] = tuple(allowed) or (None,)

	if filters.get("property"):
		conditions.append("rs.property = %(property)s")
		values["property"] = filters["property"]
	if filters.get("only_overdue"):
		conditions.append("si.outstanding_amount > 0")

	where = " AND ".join(conditions)
	data = frappe.db.sql(
		f"""
		SELECT rs.lease_contract AS lease, rs.customer, rs.property,
		       rs.sales_invoice AS invoice, rs.due_date,
		       si.grand_total AS invoiced, si.outstanding_amount AS outstanding,
		       DATEDIFF(CURDATE(), rs.due_date) AS days_overdue, si.status
		FROM `tabRent Schedule` rs
		JOIN `tabSales Invoice` si ON si.name = rs.sales_invoice
		WHERE {where}
		ORDER BY si.outstanding_amount DESC, days_overdue DESC
		""",
		values,
		as_dict=True,
	)

	columns = [
		{"label": _("Tenant"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 180},
		{"label": _("Property"), "fieldname": "property", "fieldtype": "Link", "options": "Property", "width": 130},
		{"label": _("Lease"), "fieldname": "lease", "fieldtype": "Link", "options": "Lease Contract", "width": 130},
		{"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 130},
		{"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 100},
		{"label": _("Invoiced"), "fieldname": "invoiced", "fieldtype": "Currency", "width": 110},
		{"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 120},
		{"label": _("Days Overdue"), "fieldname": "days_overdue", "fieldtype": "Int", "width": 110},
		{"label": _("Invoice Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]
	return columns, data
