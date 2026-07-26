# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Owner-portal scoping tests: guests/unlinked users get nothing; a linked owner sees
ONLY their own properties (IDOR guard)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate import portal


class TestOwnerPortalScoping(FrappeTestCase):
	def test_guest_has_no_suppliers(self):
		self.assertEqual(portal.suppliers_for_user("Guest"), [])

	def test_require_owner_blocks_unlinked_user(self):
		if portal.suppliers_for_user():  # current test user happens to be linked
			self.skipTest("Test user is linked to a Supplier")
		self.assertRaises(frappe.PermissionError, portal.owner_properties)

	def test_linked_owner_sees_only_own_property(self):
		sg = frappe.get_all("Supplier Group", pluck="name", limit=1)
		if not sg:
			self.skipTest("No Supplier Group")
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company")
		company = companies[0]

		mine = self._supplier("Bunood Owner Mine", sg[0])
		other = self._supplier("Bunood Owner Other", sg[0])
		bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "Exempt-Own", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		my_prop = frappe.get_doc({
			"doctype": "Property", "property_name": "Owner Mine Property",
			"company": company, "business_type": bt, "owner_party": mine,
		}).insert(ignore_permissions=True).name
		self.addCleanup(lambda: frappe.delete_doc("Property", my_prop, force=True, ignore_permissions=True))
		other_prop = frappe.get_doc({
			"doctype": "Property", "property_name": "Owner Other Property",
			"company": company, "business_type": bt, "owner_party": other,
		}).insert(ignore_permissions=True).name
		self.addCleanup(lambda: frappe.delete_doc("Property", other_prop, force=True, ignore_permissions=True))

		user = self._user_linked_to_supplier(mine)
		frappe.set_user(user)
		try:
			names = [p["name"] for p in portal.owner_properties()]
		finally:
			frappe.set_user("Administrator")
		self.assertIn(my_prop, names)
		self.assertNotIn(other_prop, names, "owner must never see another owner's property")

	def _supplier(self, name, group):
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_group": group}).insert(ignore_permissions=True)
		return name

	def _user_linked_to_supplier(self, supplier):
		email = "bnd-owner-mine@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({
				"doctype": "User", "email": email, "first_name": "Owner Mine",
				"send_welcome_email": 0, "roles": [],
			}).insert(ignore_permissions=True)
		# Contact linked to the user + dynamic-linked to the Supplier.
		contact = frappe.get_doc({
			"doctype": "Contact", "first_name": "Owner Mine", "user": email,
			"links": [{"link_doctype": "Supplier", "link_name": supplier}],
		})
		contact.flags.ignore_permissions = True
		contact.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("Contact", contact.name, force=True, ignore_permissions=True))
		return email
