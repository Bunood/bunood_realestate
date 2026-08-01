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
		self._validate_service_charges()

	def _validate_service_charges(self):
		"""CAM lines must never bill as rent (defense-in-depth at DEFINITION, not just at
		posting): reject a Charge Type whose Service Item is a configured rent item, and a
		revenue account override that equals a rent income account. Also: unique Charge Type
		per property (the CAM idempotency key), positive pool, area basis needs unit areas."""
		lines = self.get("service_charges") or []
		if not lines:
			return
		from bunood_realestate.real_estate.company_settings import all_configured_values

		rent_items = all_configured_values("default_rent_item")
		rent_accounts = all_configured_values("rent_income_account")
		seen = set()
		for line in lines:
			if not line.charge_type:
				continue
			if line.charge_type in seen:
				frappe.throw(_("Charge Type {0} appears twice in Service Charges — one line per type.").format(line.charge_type))
			seen.add(line.charge_type)
			if flt(line.pool_amount) <= 0:
				frappe.throw(_("Service Charge {0}: Pool / Period must be greater than zero.").format(line.charge_type))
			item = frappe.db.get_value("Charge Type", line.charge_type, "item")
			if item and item in rent_items:
				frappe.throw(
					_("Service Charge {0} uses a Rent Service Item ({1}) — CAM must use its own item so its cash is never counted as rent.").format(line.charge_type, item)
				)
			if line.revenue_account and line.revenue_account in rent_accounts:
				frappe.throw(
					_("Service Charge {0}: revenue account {1} is a Rent Income Account — pick a dedicated CAM income account.").format(line.charge_type, line.revenue_account)
				)

	def on_update(self):
		"""A CAM definition change discards the current-period Planned CAM cache so it
		re-materializes with the fresh pool/occupancy (invoiced periods are immutable).
		Only fires when the service_charges table actually changed — an unrelated Property
		save (owner phone, description…) must NOT churn/re-price not-yet-billed CAM."""
		if self._service_charges_changed():
			from bunood_realestate.real_estate.cam import resync_cam_line

			resync_cam_line(self.name)

	def _service_charges_changed(self):
		before = self.get_doc_before_save()
		if before is None:
			# First insert: only meaningful if it already carries CAM lines.
			return bool(self.get("service_charges"))
		return _cam_snapshot(self.get("service_charges")) != _cam_snapshot(before.get("service_charges"))


# The CAM-relevant fields of a service-charge line: a change to any of these re-prices the
# pool, so it must trigger a resync; a change to anything else (or an unrelated Property field)
# must not.
_CAM_FIELDS = (
	"charge_type", "pool_amount", "allocation_basis", "billing_cycle", "billing_timing",
	"vacant_policy", "charge_start_date", "charge_end_date", "is_active", "revenue_account",
	"tax_template",
)


def _cam_snapshot(lines):
	return [tuple(str(l.get(f)) for f in _CAM_FIELDS) for l in (lines or [])]


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

	# Regression-gate order preserved: payload → Settings company → user default.
	company = (
		b.get("company")
		or frappe.db.get_single_value("Real Estate Settings", "company")
		or frappe.defaults.get_user_default("Company")
	)
	if not company:
		# Multi-company: if exactly one enabled profile exists, its company is unambiguous.
		profiles = frappe.get_all(
			"Real Estate Company Profile", filters={"enabled": 1}, pluck="company"
		) if frappe.db.table_exists("Real Estate Company Profile") else []
		if len(profiles) == 1:
			company = profiles[0]
	if not company:
		frappe.throw(_("Select a company (this site serves multiple companies)."))

	# Usage (residential/commercial/mixed → VAT business type) is SEPARATE from the
	# building kind (عمارة/فيلا/… from the RE Property Type master). Old payloads sent
	# the usage under "property_type" — keep accepting it for backward compatibility.
	business_type = _business_type_for(b.get("usage_type") or b.get("property_type"))
	if not business_type:
		frappe.throw(_("No matching Business Type found — seed RE Business Type (residential/commercial) first."))

	prop = frappe.new_doc("Property")
	prop.company = company
	prop.status = "Active"
	prop.business_type = business_type
	if b.get("property_kind") and frappe.db.exists("RE Property Type", b.get("property_kind")):
		prop.property_type = b.get("property_kind")
	if b.get("construction_status") in ("Ready", "Under Construction", "On Hold"):
		prop.construction_status = b.get("construction_status")
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

	# Wizard autocomplete: an explicitly PICKED existing Supplier becomes the payout
	# party (referential integrity for managed properties); free-typed owner details
	# are still captured as plain fields below.
	if b.get("owner_party") and frappe.db.exists("Supplier", b.get("owner_party")):
		prop.owner_party = b.get("owner_party")

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
