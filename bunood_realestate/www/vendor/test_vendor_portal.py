# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Contractor-portal scoping tests: a vendor sees/updates ONLY their own work orders,
can't set Cancelled, and unlinked users are blocked (IDOR + privilege guards)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate import portal


class TestVendorPortalScoping(FrappeTestCase):
	def setUp(self):
		sg = frappe.get_all("Supplier Group", pluck="name", limit=1)
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not (sg and companies):
			self.skipTest("Need a Supplier Group and a Company")
		self.company = companies[0]
		self.mine = self._supplier("Bunood Vendor Mine", sg[0])
		self.other = self._supplier("Bunood Vendor Other", sg[0])
		bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "Exempt-Vnd", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		self.prop = frappe.get_doc({
			"doctype": "Property", "property_name": "Vendor Test Property",
			"company": self.company, "business_type": bt,
		}).insert(ignore_permissions=True).name
		self.addCleanup(lambda: frappe.delete_doc("Property", self.prop, force=True, ignore_permissions=True))
		self.my_wo = self._work_order(self.mine)
		self.other_wo = self._work_order(self.other)

	def _supplier(self, name, group):
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_group": group}).insert(ignore_permissions=True)
		return name

	def _work_order(self, contractor):
		wo = frappe.get_doc({
			"doctype": "Maintenance Work Order", "property": self.prop, "company": self.company,
			"contractor": contractor, "status": "Open",
		})
		wo.flags.ignore_permissions = True
		wo.insert(ignore_permissions=True)
		self.addCleanup(lambda n=wo.name: frappe.delete_doc("Maintenance Work Order", n, force=True, ignore_permissions=True))
		return wo.name

	def _user_for(self, supplier, email):
		if not frappe.db.exists("User", email):
			frappe.get_doc({
				"doctype": "User", "email": email, "first_name": "Vendor", "send_welcome_email": 0, "roles": [],
			}).insert(ignore_permissions=True)
		contact = frappe.get_doc({
			"doctype": "Contact", "first_name": "Vendor " + supplier, "user": email,
			"links": [{"link_doctype": "Supplier", "link_name": supplier}],
		})
		contact.flags.ignore_permissions = True
		contact.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("Contact", contact.name, force=True, ignore_permissions=True))
		return email

	def test_vendor_sees_and_updates_only_own(self):
		user = self._user_for(self.mine, "bnd-vendor-mine@example.com")
		frappe.set_user(user)
		try:
			names = [w["name"] for w in portal.vendor_work_orders()]
			self.assertIn(self.my_wo, names)
			self.assertNotIn(self.other_wo, names, "vendor must not see another vendor's work order")

			# Can update own.
			portal.update_work_order(self.my_wo, status="In Progress", notes="on site")
			self.assertEqual(frappe.db.get_value("Maintenance Work Order", self.my_wo, "status"), "In Progress")

			# Cannot update another vendor's (IDOR).
			self.assertRaises(frappe.PermissionError, portal.update_work_order, self.other_wo, "Done")

			# Cannot set a forbidden status (Cancelled).
			self.assertRaises(frappe.ValidationError, portal.update_work_order, self.my_wo, "Cancelled")
		finally:
			frappe.set_user("Administrator")

	def test_unlinked_user_blocked(self):
		if portal.suppliers_for_user():
			self.skipTest("Current user is linked to a Supplier")
		self.assertRaises(frappe.PermissionError, portal.vendor_work_orders)
