# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test: completed Maintenance Work Order → contractor Purchase Invoice in the
GL, tagged with the Property dimension and cost-centered, idempotent. Needs a site.
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.maintenance_work_order.test_work_order_gl"""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.doctype.maintenance_work_order.maintenance_work_order import (
	post_contractor_bill,
)


class TestWorkOrderGL(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured on this site")
		self.company = companies[0]
		bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "Exempt-WO", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		self.prop = frappe.db.get_value("Property", {"property_name": "WO Test Property"}) or frappe.get_doc(
			{"doctype": "Property", "property_name": "WO Test Property", "company": self.company, "business_type": bt}
		).insert(ignore_permissions=True).name
		self.unit = frappe.db.get_value(
			"Real Estate Unit", {"property": self.prop, "unit_number": "WO-1"}
		) or frappe.get_doc({
			"doctype": "Real Estate Unit", "property": self.prop, "unit_number": "WO-1", "status": "Vacant",
		}).insert(ignore_permissions=True).name

		self.contractor = "Bunood Test Contractor"
		if not frappe.db.exists("Supplier", self.contractor):
			frappe.get_doc({
				"doctype": "Supplier", "supplier_name": self.contractor,
				"supplier_group": frappe.get_all("Supplier Group", pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)

		self.item = "Bunood Maintenance Service"
		if not frappe.db.exists("Item", self.item):
			frappe.get_doc({
				"doctype": "Item", "item_code": self.item, "item_name": self.item, "is_stock_item": 0,
				"item_group": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		expense = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Expense", "is_group": 0}, pluck="name", limit=1
		)
		if not expense:
			self.skipTest("No expense account on the company")
		settings = frappe.get_single("Real Estate Settings")
		settings.maintenance_item = self.item
		settings.maintenance_expense_account = expense[0]
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)

		# maintenance_request is mandatory on the work order, and property/unit/company are
		# read_only fetch_from the request — so the request must carry them.
		self.request = frappe.get_doc({
			"doctype": "Maintenance Request", "subject": "WO GL test",
			"property": self.prop, "unit": self.unit, "status": "Open", "priority": "Medium",
		}).insert(ignore_permissions=True).name
		self.addCleanup(lambda: frappe.delete_doc("Maintenance Request", self.request, force=True, ignore_permissions=True))

	def _make_done_work_order(self, rate=500):
		# property / unit / company are fetch_from maintenance_request — do not set them here.
		wo = frappe.get_doc({
			"doctype": "Maintenance Work Order",
			"maintenance_request": self.request,
			"contractor": self.contractor,
			"status": "Done",
			"scheduled_date": "2026-04-10",
			"items": [{"item": "Plumbing", "qty": 1, "rate": rate}],
		})
		wo.flags.ignore_permissions = True
		wo.insert(ignore_permissions=True)
		self.addCleanup(lambda n=wo.name: frappe.delete_doc("Maintenance Work Order", n, force=True, ignore_permissions=True))
		return wo

	def test_done_work_order_posts_property_tagged_contractor_bill(self):
		"""total_cost → Purchase Invoice to the contractor, Property-tagged + cost-centered."""
		if "property" not in frappe.get_meta("Purchase Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		wo = self._make_done_work_order(rate=500)
		self.assertEqual(wo.total_cost, 500)

		res = post_contractor_bill(wo.name)
		self.addCleanup(self._cancel_pi, res["purchase_invoice"])
		self.assertFalse(res.get("already"))

		pi = frappe.get_doc("Purchase Invoice", res["purchase_invoice"])
		self.assertEqual(pi.supplier, self.contractor)
		self.assertEqual(round(pi.items[0].amount, 2), 500.0)
		self.assertEqual(pi.items[0].get("property"), self.prop, "expense must carry the Property dimension")
		self.assertTrue(pi.items[0].cost_center, "P&L expense line needs a cost center")
		self.assertEqual(frappe.db.get_value("Maintenance Work Order", wo.name, "purchase_invoice"), pi.name)

	def test_second_post_is_idempotent(self):
		"""A re-click must not post a second bill — the work order's link is the guard."""
		if "property" not in frappe.get_meta("Purchase Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		wo = self._make_done_work_order(rate=750)
		res1 = post_contractor_bill(wo.name)
		self.addCleanup(self._cancel_pi, res1["purchase_invoice"])
		res2 = post_contractor_bill(wo.name)
		self.assertTrue(res2.get("already"))
		self.assertEqual(res1["purchase_invoice"], res2["purchase_invoice"])

	def test_refuses_when_not_done(self):
		wo = self._make_done_work_order(rate=100)
		wo.db_set("status", "In Progress")
		self.assertRaises(frappe.ValidationError, post_contractor_bill, wo.name)

	def test_cancelling_bill_clears_link_and_allows_reposting(self):
		"""Cancelling the contractor PI must clear the work order link so a corrected bill can
		be re-posted (regression for the 'stuck already-billed' idempotency hole)."""
		if "property" not in frappe.get_meta("Purchase Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		wo = self._make_done_work_order(rate=400)
		res1 = post_contractor_bill(wo.name)
		frappe.get_doc("Purchase Invoice", res1["purchase_invoice"]).cancel()
		self.assertIsNone(
			frappe.db.get_value("Maintenance Work Order", wo.name, "purchase_invoice"),
			"PI cancel must clear the work order's purchase_invoice link",
		)
		res2 = post_contractor_bill(wo.name)
		self.addCleanup(self._cancel_pi, res2["purchase_invoice"])
		self.assertFalse(res2.get("already"))
		self.assertNotEqual(res1["purchase_invoice"], res2["purchase_invoice"])

	def _cancel_pi(self, name):
		try:
			pi = frappe.get_doc("Purchase Invoice", name)
			if pi.docstatus == 1:
				pi.cancel()
		except Exception:
			pass
