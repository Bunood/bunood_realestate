# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test: importing an existing (mid-term) contract with import_historical_seed
posts ONE is_opening Sales Invoice for the carried balance and does NOT create back-dated
rent rows. Needs a configured site.
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.lease_contract.test_opening_balance"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, nowdate


class TestOpeningBalanceImport(FrappeTestCase):
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
		self.prop = frappe.db.get_value("Property", {"property_name": "Bunood Opening Property"}) or frappe.get_doc({
			"doctype": "Property", "property_name": "Bunood Opening Property",
			"company": self.company, "business_type": "Residential-Test",
		}).insert(ignore_permissions=True).name
		self.unit = frappe.db.get_value(
			"Real Estate Unit", {"property": self.prop, "unit_number": "OPN-1"}
		) or frappe.get_doc({
			"doctype": "Real Estate Unit", "property": self.prop, "unit_number": "OPN-1", "status": "Vacant",
		}).insert(ignore_permissions=True).name

		item = "Bunood Test Rent"
		if not frappe.db.exists("Item", item):
			frappe.get_doc({
				"doctype": "Item", "item_code": item, "item_name": item, "is_stock_item": 0,
				"item_group": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		opening_acc = frappe.get_all(
			"Account",
			filters={"company": self.company, "is_group": 0, "root_type": ["in", ["Liability", "Asset"]]},
			pluck="name", limit=1,
		)
		if not opening_acc:
			self.skipTest("No balance-sheet account to use as Opening Balance Account")
		settings = frappe.get_single("Real Estate Settings")
		settings.default_rent_item = item
		settings.opening_balance_account = opening_acc[0]
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)

	def test_seed_posts_opening_invoice_and_skips_backdated_rows(self):
		lease = frappe.get_doc({
			"doctype": "Lease Contract",
			"customer": "Bunood Test Tenant",
			"company": self.company,
			"contract_type": "Residential",
			"start_date": "2020-01-01",   # started long before "today"
			"end_date": "2027-12-31",     # runs into the future
			"billing_cycle": "Monthly",
			"import_historical_seed": 1,
			"import_contract_total": 5000,
			"units": [{"unit": self.unit, "annual_rent": 120000}],
		})
		lease.insert(ignore_permissions=True)
		lease.submit()
		self.addCleanup(self._cleanup_lease, lease.name)

		# 1) An is_opening Sales Invoice was posted for the carried balance, linked to the lease.
		opening = frappe.db.get_value("Lease Contract", lease.name, "opening_invoice")
		self.assertTrue(opening, "an opening invoice should be created for a seeded import")
		si = frappe.get_doc("Sales Invoice", opening)
		self.assertEqual(si.is_opening, "Yes")
		self.assertEqual(round(si.grand_total, 2), 5000.0)
		self.assertEqual(si.docstatus, 1)
		self.assertEqual(si.items[0].income_account, frappe.db.get_single_value("Real Estate Settings", "opening_balance_account"))

		# 2) No back-dated rent rows: every generated period is due today or later.
		rows = frappe.get_all("Rent Schedule", filters={"lease_contract": lease.name}, pluck="period_start")
		self.assertTrue(rows, "future rent rows should still be planned")
		today = getdate(nowdate())
		self.assertTrue(all(getdate(d) >= today for d in rows), "no rent period may be due before today")

	def _cleanup_lease(self, name):
		try:
			doc = frappe.get_doc("Lease Contract", name)
			opening = doc.opening_invoice
			if opening:
				pi = frappe.get_doc("Sales Invoice", opening)
				if pi.docstatus == 1:
					pi.cancel()
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Lease Contract", name, force=True, ignore_permissions=True)
		except Exception:
			pass
