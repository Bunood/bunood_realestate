# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test for lease auto-expiry (needs a configured site with a Company).
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.lease_contract.test_auto_expiry"""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.doctype.lease_contract.lease_contract import expire_due_leases


class TestLeaseAutoExpiry(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured on this site")
		self.company = companies[0]
		if not frappe.db.exists("RE Business Type", "Residential-Test"):
			frappe.get_doc({
				"doctype": "RE Business Type", "title": "Residential-Test", "vat_treatment": "Exempt"
			}).insert(ignore_permissions=True)
		if not frappe.db.exists("Customer", "Bunood Test Tenant"):
			frappe.get_doc({
				"doctype": "Customer", "customer_name": "Bunood Test Tenant",
				"customer_group": frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
				"territory": frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		self.prop = frappe.db.get_value("Property", {"property_name": "Bunood Expiry Property"}) or frappe.get_doc({
			"doctype": "Property", "property_name": "Bunood Expiry Property",
			"company": self.company, "business_type": "Residential-Test",
		}).insert(ignore_permissions=True).name
		self.unit = frappe.db.get_value(
			"Real Estate Unit", {"property": self.prop, "unit_number": "EXP-1"}
		) or frappe.get_doc({
			"doctype": "Real Estate Unit", "property": self.prop, "unit_number": "EXP-1", "status": "Vacant",
		}).insert(ignore_permissions=True).name

	def test_expire_due_lease_frees_unit(self):
		"""An Active lease whose end_date has passed is moved to Expired and its unit freed."""
		lease = frappe.get_doc({
			"doctype": "Lease Contract",
			"customer": "Bunood Test Tenant",
			"company": self.company,
			"contract_type": "Residential",
			"start_date": "2020-01-01",
			"end_date": "2020-12-31",  # firmly in the past
			"billing_cycle": "Monthly",
			"units": [{"unit": self.unit, "annual_rent": 120000}],
		})
		lease.insert(ignore_permissions=True)
		lease.submit()
		self.addCleanup(self._cleanup_lease, lease.name)
		# Precondition: submitting occupied the unit.
		self.assertEqual(frappe.db.get_value("Real Estate Unit", self.unit, "status"), "Occupied")

		expire_due_leases()

		self.assertEqual(frappe.db.get_value("Lease Contract", lease.name, "status"), "Expired")
		self.assertEqual(frappe.db.get_value("Real Estate Unit", self.unit, "status"), "Vacant")
		self.assertIsNone(frappe.db.get_value("Real Estate Unit", self.unit, "current_lease"))

	def test_active_future_lease_is_left_alone(self):
		"""A still-running lease (end_date in the future) must NOT be expired."""
		lease = frappe.get_doc({
			"doctype": "Lease Contract",
			"customer": "Bunood Test Tenant",
			"company": self.company,
			"contract_type": "Residential",
			"start_date": "2099-01-01",
			"end_date": "2099-12-31",  # firmly in the future
			"billing_cycle": "Monthly",
			"units": [{"unit": self.unit, "annual_rent": 120000}],
		})
		lease.insert(ignore_permissions=True)
		lease.submit()
		self.addCleanup(self._cleanup_lease, lease.name)

		expire_due_leases()

		self.assertEqual(frappe.db.get_value("Lease Contract", lease.name, "status"), "Active")
		self.assertEqual(frappe.db.get_value("Real Estate Unit", self.unit, "status"), "Occupied")

	def _cleanup_lease(self, name):
		try:
			doc = frappe.get_doc("Lease Contract", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Lease Contract", name, force=True, ignore_permissions=True)
		except Exception:
			pass
