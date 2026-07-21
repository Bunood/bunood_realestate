# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	conditions = ["1 = 1"]
	values = {}
	# Enforce company User Permissions (Script Report SQL is not auto-scoped by Frappe).
	allowed = frappe.get_list("Company", pluck="name")
	if filters.get("company"):
		if filters["company"] not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("p.company = %(company)s")
		values["company"] = filters["company"]
	else:
		conditions.append("p.company IN %(allowed_companies)s")
		values["allowed_companies"] = tuple(allowed) or (None,)
	if filters.get("property"):
		conditions.append("reu.property = %(property)s")
		values["property"] = filters["property"]
	where = " AND ".join(conditions)

	data = frappe.db.sql(
		f"""
		SELECT reu.property AS property,
		       COUNT(*) AS total_units,
		       SUM(CASE WHEN reu.status = 'Occupied' THEN 1 ELSE 0 END) AS occupied,
		       SUM(CASE WHEN reu.status = 'Vacant' THEN 1 ELSE 0 END) AS vacant,
		       SUM(CASE WHEN reu.status = 'Reserved' THEN 1 ELSE 0 END) AS reserved,
		       SUM(CASE WHEN reu.status = 'Maintenance' THEN 1 ELSE 0 END) AS maintenance
		FROM `tabReal Estate Unit` reu
		JOIN `tabProperty` p ON p.name = reu.property
		WHERE {where}
		GROUP BY reu.property
		ORDER BY reu.property
		""",
		values,
		as_dict=True,
	)

	for row in data:
		total = flt(row.get("total_units"))
		row["occupancy_pct"] = round(flt(row.get("occupied")) / total * 100, 1) if total else 0

	columns = [
		{"label": _("Property"), "fieldname": "property", "fieldtype": "Link", "options": "Property", "width": 180},
		{"label": _("Total Units"), "fieldname": "total_units", "fieldtype": "Int", "width": 100},
		{"label": _("Occupied"), "fieldname": "occupied", "fieldtype": "Int", "width": 90},
		{"label": _("Vacant"), "fieldname": "vacant", "fieldtype": "Int", "width": 90},
		{"label": _("Reserved"), "fieldname": "reserved", "fieldtype": "Int", "width": 90},
		{"label": _("Maintenance"), "fieldname": "maintenance", "fieldtype": "Int", "width": 110},
		{"label": _("Occupancy %"), "fieldname": "occupancy_pct", "fieldtype": "Percent", "width": 110},
	]
	return columns, data
