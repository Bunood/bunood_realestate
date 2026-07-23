# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Tests for the maintenance conversation thread + its portal access scoping."""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.portal import _owned_request
from bunood_realestate.real_estate.doctype.maintenance_request.maintenance_request import add_update, append_update


class TestMaintenanceConversation(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "Exempt-Mnt", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		self.prop = frappe.db.get_value("Property", {"property_name": "Mnt Test Property"}) or frappe.get_doc(
			{"doctype": "Property", "property_name": "Mnt Test Property", "company": self.company, "business_type": bt}
		).insert(ignore_permissions=True).name

	def _make_request(self, tenant=None):
		doc = frappe.get_doc({
			"doctype": "Maintenance Request", "subject": "Leaky tap",
			"property": self.prop, "status": "Open", "priority": "Medium",
		})
		if tenant:
			doc.tenant = tenant
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		self.addCleanup(lambda n=doc.name: frappe.delete_doc("Maintenance Request", n, force=True, ignore_permissions=True))
		return doc

	def test_add_update_appends_stamped_entry(self):
		doc = self._make_request()
		add_update(request=doc.name, message="  Technician scheduled tomorrow  ")
		doc.reload()
		self.assertEqual(len(doc.updates), 1)
		u = doc.updates[0]
		self.assertEqual(u.message, "Technician scheduled tomorrow")  # trimmed
		self.assertEqual(u.author, frappe.session.user)
		self.assertEqual(u.from_portal, 0)
		self.assertTrue(u.posted_on)

	def test_empty_update_is_rejected(self):
		doc = self._make_request()
		self.assertRaises(frappe.ValidationError, append_update, doc, "   ", None, 0)
		self.assertRaises(frappe.ValidationError, append_update, doc, None, None, 0)

	def test_portal_flag_recorded(self):
		doc = self._make_request()
		append_update(doc, "من المستأجر: الحنفية تنقط", None, from_portal=1)
		doc.reload()
		self.assertEqual(doc.updates[0].from_portal, 1)

	def test_owned_request_scoping_blocks_other_tenant(self):
		"""The portal ownership check must reject a request that isn't the caller's — IDOR guard."""
		cust = frappe.get_all("Customer", pluck="name", limit=2)
		if not cust:
			self.skipTest("No Customer to bind ownership")
		owner_customer = cust[0]
		doc = self._make_request(tenant=owner_customer)
		# Caller whose customers do NOT include the owner → PermissionError.
		self.assertRaises(frappe.PermissionError, _owned_request, doc.name, ["Some Other Customer ZZZ"])
		# Caller who owns it → returns the name.
		self.assertEqual(_owned_request(doc.name, [owner_customer]), doc.name)
