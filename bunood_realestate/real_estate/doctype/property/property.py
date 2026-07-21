# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Property(Document):
	pass


@frappe.whitelist()
def create_units(property, count, unit_type=None, prefix="", start=1, floor=None):
	"""Bulk-create Real Estate Units under a property. Called from the Property form.
	Names are unique per (property, unit_number) via the child doctype autoname."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	count = int(count)
	if count < 1 or count > 500:
		frappe.throw(_("Number of units must be between 1 and 500."))
	start = int(start or 1)
	created = []
	for i in range(start, start + count):
		unit = frappe.new_doc("Real Estate Unit")
		unit.property = property
		unit.unit_number = f"{prefix}{i}"
		if unit_type:
			unit.unit_type = unit_type
		if floor:
			unit.floor = floor
		unit.status = "Vacant"
		unit.insert()
		created.append(unit.name)
	return created
