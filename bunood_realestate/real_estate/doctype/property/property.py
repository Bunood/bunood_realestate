# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class Property(Document):
	def validate(self):
		# One legal deed = one Property. Reject a duplicate deed (per company) so a
		# double-click / wizard re-run can't split one building into two GL anchors.
		if self.deed_number:
			dupe = frappe.db.get_value(
				"Property",
				{"deed_number": self.deed_number, "company": self.company, "name": ["!=", self.name or ""]},
				"name",
			)
			if dupe:
				frappe.throw(
					_("Property {0} already uses deed number {1}.").format(dupe, self.deed_number)
				)


# Card value -> master record resolvers (the wizard shows friendly cards; we map
# them onto the user-editable RE Business Type / RE Management Model masters).
def _business_type_for(card):
	vat = {"residential": "Exempt", "commercial": "Standard", "mixed": "Standard"}.get(card)
	if not vat:
		return None
	return frappe.db.get_value("RE Business Type", {"vat_treatment": vat}, "name")


def _management_model_for(card):
	if not card:
		return None
	return frappe.db.get_value("RE Management Model", {"behavior": card}, "name")


_UNIT_TYPES = {"Apartment", "Shop", "Office", "Villa", "Warehouse", "Land Plot", "Other"}


@frappe.whitelist()
def create_property_with_units(data):
	"""Atomically create a Property and its generated Units from the new-property
	wizard. One request = one transaction, so a failure rolls back the whole set."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	payload = json.loads(data) if isinstance(data, str) else (data or {})
	b = payload.get("property") or {}
	units = payload.get("units") or []

	if not (b.get("property_name") or "").strip():
		frappe.throw(_("Property name is required."))

	company = (
		b.get("company")
		or frappe.db.get_single_value("Real Estate Settings", "company")
		or frappe.defaults.get_user_default("Company")
	)
	if not company:
		frappe.throw(_("No company configured — set one in Real Estate Settings."))

	business_type = _business_type_for(b.get("property_type"))
	if not business_type:
		frappe.throw(_("No matching Business Type found — seed RE Business Type (residential/commercial) first."))

	prop = frappe.new_doc("Property")
	prop.company = company
	prop.status = "Active"
	prop.business_type = business_type
	prop.property_name = b.get("property_name").strip()
	prop.residential_subtype = b.get("residential_subtype")
	prop.code = b.get("code")
	prop.deed_number = b.get("deed_number")
	prop.construction_year = cint(b.get("construction_year")) or None
	prop.total_area_sqm = flt(b.get("total_area_sqm")) or None
	prop.floors_count = cint(b.get("floors_count")) or None

	mgmt = _management_model_for(b.get("operation_type"))
	if mgmt:
		prop.management_model = mgmt
	if b.get("management_fee_percentage"):
		prop.management_fee_percentage = flt(b.get("management_fee_percentage"))

	for f in (
		"owner_name", "owner_phone", "owner_id_num", "owner_email", "owner_iban",
		"owner_nationality", "owner_date_of_birth", "owner_address",
		"city", "district", "street", "building_no", "postal_code", "description",
	):
		if b.get(f):
			prop.set(f, b.get(f))
	prop.insert()

	created = 0
	for i, u in enumerate(units, start=1):
		unit = frappe.new_doc("Real Estate Unit")
		unit.property = prop.name
		unit.unit_number = (u.get("unit_number") or f"U-{i}")[:140]
		utype = u.get("unit_type")
		unit.unit_type = utype if utype in _UNIT_TYPES else "Apartment"
		unit.floor = cint(u.get("floor"))
		unit.rooms_count = cint(u.get("rooms_count"))
		unit.living_rooms_count = cint(u.get("living_rooms_count"))
		unit.bathrooms = cint(u.get("bathrooms"))
		unit.area_sqm = flt(u.get("area_sqm")) or None
		# The wizard captures MONTHLY rent; the unit stores annual market rent.
		unit.market_rent = flt(u.get("monthly_rent")) * 12
		unit.deposit_amount = flt(u.get("deposit_amount"))
		unit.status = "Vacant"
		unit.insert()
		created += 1

	return {"property": prop.name, "units": created}


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
