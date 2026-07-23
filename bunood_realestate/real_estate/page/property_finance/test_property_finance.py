# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test for the per-property finance hub (needs a site with the Property
accounting dimension migrated onto GL Entry)."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from bunood_realestate.real_estate.property_finance import property_finance


class TestPropertyFinance(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		if "property" not in frappe.get_meta("GL Entry").fields_map:
			self.skipTest("Property accounting dimension not migrated onto GL Entry")
		self.cc = frappe.get_cached_value("Company", self.company, "cost_center")
		inc = frappe.get_all("Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1)
		exp = frappe.get_all("Account", filters={"company": self.company, "root_type": "Expense", "is_group": 0}, pluck="name", limit=1)
		cash = frappe.get_all("Account", filters={"company": self.company, "account_type": ["in", ["Bank", "Cash"]], "is_group": 0}, pluck="name", limit=1)
		if not (self.cc and inc and exp and cash):
			self.skipTest("Company missing cost center / income / expense / cash account")
		self.inc, self.exp, self.cash = inc[0], exp[0], cash[0]
		bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "Exempt-PF", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		self.prop = frappe.get_doc({
			"doctype": "Property", "property_name": "PF Test Property", "company": self.company, "business_type": bt,
		}).insert(ignore_permissions=True).name
		self.addCleanup(lambda: frappe.delete_doc("Property", self.prop, force=True, ignore_permissions=True))

	def _post_rent_income(self, amount, on="2026-05-10"):
		customer = "PF Test Tenant"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc({
				"doctype": "Customer", "customer_name": customer,
				"customer_group": frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
				"territory": frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		item = "PF Test Rent"
		if not frappe.db.exists("Item", item):
			frappe.get_doc({
				"doctype": "Item", "item_code": item, "item_name": item, "is_stock_item": 0,
				"item_group": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		si = frappe.get_doc({
			"doctype": "Sales Invoice", "customer": customer, "company": self.company,
			"posting_date": on, "set_posting_time": 1, "due_date": on,
			"items": [{"item_code": item, "qty": 1, "rate": amount, "income_account": self.inc, "cost_center": self.cc, "property": self.prop}],
		})
		si.flags.ignore_permissions = True
		si.insert(ignore_permissions=True)
		si.submit()
		self.addCleanup(lambda n=si.name: self._cancel("Sales Invoice", n))
		return si.name

	def _post_expense(self, amount, on="2026-05-12"):
		ex = frappe.get_doc({
			"doctype": "Property Expense", "property": self.prop, "category": "Utilities",
			"expense_date": on, "amount": amount, "expense_account": self.exp, "paid_from": self.cash,
		})
		ex.insert(ignore_permissions=True)
		ex.submit()
		self.addCleanup(lambda n=ex.name: self._cancel("Property Expense", n))
		return ex.name

	def _cancel(self, dt, name):
		try:
			d = frappe.get_doc(dt, name)
			if d.docstatus == 1:
				d.cancel()
		except Exception:
			pass

	def test_income_minus_expense_equals_net_from_gl(self):
		self._post_rent_income(1000)
		self._post_expense(300)
		data = property_finance(self.prop)
		self.assertEqual(data["total_income"], 1000.0)
		self.assertEqual(data["total_expense"], 300.0)
		self.assertEqual(data["net"], 700.0)
		self.assertEqual(data["property"], self.prop)
		# Occupancy structure present and sane.
		self.assertIn("pct", data["occupancy"])

	def test_period_filter_excludes_outside(self):
		self._post_rent_income(1000, on="2026-05-10")
		# A window entirely before the posting → nothing counted.
		data = property_finance(self.prop, from_date="2026-01-01", to_date="2026-01-31")
		self.assertEqual(data["total_income"], 0.0)

	def test_missing_property_raises(self):
		self.assertRaises(frappe.DoesNotExistError, property_finance, "No Such Property ZZZ")
