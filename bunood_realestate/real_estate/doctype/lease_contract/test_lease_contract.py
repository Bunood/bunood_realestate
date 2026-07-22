# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test (needs a configured site with a Company).
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.lease_contract.test_lease_contract"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestLeaseContract(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured on this site")
		self.company = companies[0]
		self._ensure("RE Business Type", "Residential-Test", {"vat_treatment": "Exempt"})
		self.customer = self._ensure("Customer", "Bunood Test Tenant", {"customer_name": "Bunood Test Tenant"})
		self.prop = self._ensure_property()
		self.unit = self._ensure_unit()

	def _ensure(self, dt, title, values):
		key = "title" if frappe.get_meta(dt).has_field("title") else "name"
		if frappe.db.exists(dt, title):
			return title
		doc = {"doctype": dt}
		doc.update(values)
		if frappe.get_meta(dt).has_field("title"):
			doc["title"] = title
		d = frappe.get_doc(doc)
		d.insert(ignore_permissions=True)
		return d.name

	def _ensure_property(self):
		name = frappe.db.get_value("Property", {"property_name": "Bunood Test Property"})
		if name:
			return name
		p = frappe.get_doc({
			"doctype": "Property",
			"property_name": "Bunood Test Property",
			"company": self.company,
			"business_type": "Residential-Test",
		})
		p.insert(ignore_permissions=True)
		return p.name

	def _ensure_unit(self):
		name = frappe.db.get_value("Real Estate Unit", {"property": self.prop, "unit_number": "T-1"})
		if name:
			return name
		u = frappe.get_doc({
			"doctype": "Real Estate Unit",
			"property": self.prop,
			"unit_number": "T-1",
			"status": "Vacant",
		})
		u.insert(ignore_permissions=True)
		return u.name

	def test_submit_generates_monthly_schedule_and_occupies_unit(self):
		lease = frappe.get_doc({
			"doctype": "Lease Contract",
			"customer": self.customer,
			"company": self.company,
			"contract_type": "Residential",
			"start_date": "2026-01-01",
			"end_date": "2026-12-31",
			"billing_cycle": "Monthly",
			"units": [{"unit": self.unit, "annual_rent": 120000}],
		})
		lease.insert(ignore_permissions=True)
		lease.submit()
		self.addCleanup(self._cleanup_lease, lease.name)

		rows = frappe.get_all(
			"Rent Schedule", filters={"lease_contract": lease.name}, fields=["base_amount"]
		)
		self.assertEqual(len(rows), 12)
		self.assertEqual(round(sum(r.base_amount for r in rows), 2), 120000)
		self.assertEqual(frappe.db.get_value("Real Estate Unit", self.unit, "status"), "Occupied")

	def _cleanup_lease(self, name):
		try:
			doc = frappe.get_doc("Lease Contract", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Lease Contract", name, force=True, ignore_permissions=True)
		except Exception:
			pass
