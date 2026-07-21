# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	conditions = ["lc.docstatus = 1"]
	values = {}
	# Enforce company User Permissions: Script Report SQL is not auto-scoped by Frappe,
	# so a user must not read companies they are not permitted to see.
	allowed = frappe.get_list("Company", pluck="name")
	if filters.get("company"):
		if filters["company"] not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("lc.company = %(company)s")
		values["company"] = filters["company"]
	else:
		conditions.append("lc.company IN %(allowed_companies)s")
		values["allowed_companies"] = tuple(allowed) or (None,)
	if filters.get("property"):
		conditions.append("lc.property = %(property)s")
		values["property"] = filters["property"]
	if filters.get("status"):
		conditions.append("lc.status = %(status)s")
		values["status"] = filters["status"]
	where = " AND ".join(conditions)

	data = frappe.db.sql(
		f"""
		SELECT lc.name AS lease, lc.customer, lc.property, lu.unit,
		       reu.unit_type, lu.annual_rent, lc.contract_type,
		       lc.start_date, lc.end_date, lc.status
		FROM `tabLease Contract` lc
		JOIN `tabLease Unit` lu ON lu.parent = lc.name
		LEFT JOIN `tabReal Estate Unit` reu ON reu.name = lu.unit
		WHERE {where}
		ORDER BY lc.property, lu.unit
		""",
		values,
		as_dict=True,
	)

	columns = [
		{"label": _("Lease"), "fieldname": "lease", "fieldtype": "Link", "options": "Lease Contract", "width": 110},
		{"label": _("Tenant"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Property"), "fieldname": "property", "fieldtype": "Link", "options": "Property", "width": 120},
		{"label": _("Unit"), "fieldname": "unit", "fieldtype": "Link", "options": "Real Estate Unit", "width": 150},
		{"label": _("Type"), "fieldname": "unit_type", "fieldtype": "Data", "width": 90},
		{"label": _("Annual Rent"), "fieldname": "annual_rent", "fieldtype": "Currency", "width": 120},
		{"label": _("Contract"), "fieldname": "contract_type", "fieldtype": "Data", "width": 90},
		{"label": _("Start"), "fieldname": "start_date", "fieldtype": "Date", "width": 95},
		{"label": _("End"), "fieldname": "end_date", "fieldtype": "Date", "width": 95},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 80},
	]
	return columns, data
