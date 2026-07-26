# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test for the building-view data endpoint (needs a site)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.previews import property_building


class TestPropertyBuilding(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "Exempt-Bld", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		self.prop = frappe.get_doc({
			"doctype": "Property", "property_name": "Bld Test Property", "company": self.company, "business_type": bt,
		}).insert(ignore_permissions=True).name
		self.addCleanup(lambda: frappe.delete_doc("Property", self.prop, force=True, ignore_permissions=True))
		for i, floor in enumerate([1, 1, 2]):
			frappe.get_doc({
				"doctype": "Real Estate Unit", "property": self.prop,
				"unit_number": f"U{i+1}", "unit_type": "Apartment", "floor": floor,
				"area_sqm": 100, "status": "Vacant", "market_rent": 1000,
			}).insert(ignore_permissions=True)

	def test_groups_by_floor_top_first_and_counts(self):
		d = property_building(self.prop)
		self.assertEqual(d["totals"]["total"], 3)
		self.assertEqual(d["totals"]["occupied"], 0)
		self.assertEqual(d["totals"]["vacant"], 3)
		self.assertEqual(d["totals"]["occupancy_pct"], 0.0)
		# Floors sorted descending (top floor first) for the building view.
		floors = [f["floor"] for f in d["floors"]]
		self.assertEqual(floors, [2, 1])
		self.assertEqual(len(d["floors"][0]["units"]), 1)  # floor 2 has one unit
		self.assertEqual(len(d["floors"][1]["units"]), 2)  # floor 1 has two
		# No active lease → never "Occupied".
		for f in d["floors"]:
			for u in f["units"]:
				self.assertEqual(u["state"], "Vacant")
				self.assertIsNone(u["tenant"])

	def test_missing_property_raises(self):
		self.assertRaises(frappe.DoesNotExistError, property_building, "No Such Property ZZZ")
