# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration tests for the Charge Engine (needs a configured site).
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.charge_schedule.test_charge_engine_integration"""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.charge_engine import (
	generate_due_charge_invoices,
	seed_charges_for_lease,
)


class TestChargeEngine(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated")
		self.income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		if not self.income:
			self.skipTest("No income account")
		self.income = self.income[0]

		# A Utility Charge Type carrying a Service Item (income account resolves from it).
		item = "Bunood CE Test Utility Item"
		if not frappe.db.exists("Item", item):
			doc = frappe.get_doc({
				"doctype": "Item", "item_code": item, "item_name": item, "is_stock_item": 0,
				"item_group": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
			})
			doc.append("item_defaults", {"company": self.company, "income_account": self.income})
			doc.insert(ignore_permissions=True)
		self.charge_type = "Bunood CE Electricity"
		if not frappe.db.exists("Charge Type", self.charge_type):
			frappe.get_doc({
				"doctype": "Charge Type", "charge_type_name": self.charge_type,
				"charge_kind": "Utility", "item": item, "is_active": 1, "is_recurring": 1,
			}).insert(ignore_permissions=True)

		settings = frappe.get_single("Real Estate Settings")
		settings.auto_submit_invoices = 1
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)

		self.prop = self._property()
		self.unit = self._unit(self.prop)
		self.customer = self._customer()

	# ---- fixtures ------------------------------------------------------------
	def _property(self):
		bt = frappe.db.get_value("RE Business Type", {}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "CE-BT", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		return frappe.get_doc({
			"doctype": "Property", "property_name": "Bunood CE Property",
			"company": self.company, "business_type": bt,
		}).insert(ignore_permissions=True).name

	def _unit(self, prop):
		return frappe.get_doc({
			"doctype": "Real Estate Unit", "unit_number": "CE-U1", "property": prop, "status": "Vacant",
		}).insert(ignore_permissions=True).name

	def _customer(self):
		name = "Bunood CE Tenant"
		if not frappe.db.exists("Customer", name):
			frappe.get_doc({
				"doctype": "Customer", "customer_name": name,
				"customer_group": frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
				"territory": frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		return name

	def _lease_with_charge(self, method="Fixed", amount=200, tariff=0, cycle="Monthly"):
		lease = frappe.get_doc({
			"doctype": "Lease Contract",
			"customer": self.customer, "property": self.prop, "company": self.company,
			"contract_type": "Residential", "billing_cycle": "Monthly",
			"start_date": "2026-01-01", "end_date": "2026-03-31",
			"annual_rent_total": 120000,
			"units": [{"unit": self.unit, "annual_rent": 120000}],
			"charges": [{
				"charge_type": self.charge_type, "billing_method": method,
				"amount": amount, "billing_cycle": cycle, "billing_timing": "Arrears",
				"unit": self.unit, "tariff": tariff, "previous_reading": 0, "is_active": 1,
			}],
		})
		lease.flags.ignore_permissions = True
		lease.insert(ignore_permissions=True)
		lease.submit()
		self.addCleanup(self._cancel_lease, lease.name)
		return lease

	# ---- tests ---------------------------------------------------------------
	def test_fixed_charge_seeds_and_bills_with_dimensions(self):
		lease = self._lease_with_charge(method="Fixed", amount=200, cycle="Monthly")
		rows = frappe.get_all(
			"Charge Schedule", filters={"lease_contract": lease.name},
			fields=["name", "status", "base_amount", "due_date", "period_end"],
		)
		self.assertEqual(len(rows), 3)  # Jan/Feb/Mar
		self.assertTrue(all(r.status == "Planned" for r in rows))
		self.assertEqual(sorted(r.base_amount for r in rows), [200, 200, 200])
		# Arrears → due on period end.
		self.assertTrue(all(str(r.due_date) == str(r.period_end) for r in rows))

		created = generate_due_charge_invoices(lease_contract=lease.name)
		self.assertGreaterEqual(created, 1)
		invoiced = frappe.get_all(
			"Charge Schedule", filters={"lease_contract": lease.name, "status": "Invoiced"},
			fields=["sales_invoice"],
		)
		self.assertTrue(invoiced)
		si = frappe.get_doc("Sales Invoice", invoiced[0].sales_invoice)
		line = si.items[0]
		self.assertEqual(line.get("property"), self.prop, "charge line must carry the property dimension")
		self.assertEqual(line.get("real_estate_unit"), self.unit)
		self.assertEqual(line.income_account, self.income, "line income account resolves from the Charge Type item")

	def test_metered_charge_awaits_reading_then_bills(self):
		lease = self._lease_with_charge(method="Metered", amount=0, tariff=0.5, cycle="Monthly")
		awaiting = frappe.get_all(
			"Charge Schedule", filters={"lease_contract": lease.name, "status": "Awaiting Reading"}, pluck="name"
		)
		self.assertEqual(len(awaiting), 3, "metered periods start Awaiting Reading")
		# Generator must NOT bill an Awaiting-Reading row.
		self.assertEqual(generate_due_charge_invoices(lease_contract=lease.name), 0)

		charge_row = lease.charges[0].name
		reading = frappe.get_doc({
			"doctype": "Meter Reading", "lease_contract": lease.name, "lease_charge_row": charge_row,
			"charge_schedule": sorted(awaiting)[0], "reading_date": "2026-01-31",
			"previous_reading": 0, "current_reading": 100,
		})
		reading.flags.ignore_permissions = True
		reading.insert(ignore_permissions=True)
		reading.submit()
		self.addCleanup(self._cancel, "Meter Reading", reading.name)

		row = frappe.get_doc("Charge Schedule", sorted(awaiting)[0])
		self.assertEqual(row.status, "Planned")
		self.assertEqual(row.consumption, 100)
		self.assertEqual(row.base_amount, 50.0)  # 100 x 0.5

	def test_sales_invoice_cancel_resets_charge_row(self):
		lease = self._lease_with_charge(method="Fixed", amount=200, cycle="Monthly")
		generate_due_charge_invoices(lease_contract=lease.name)
		inv = frappe.get_all(
			"Charge Schedule", filters={"lease_contract": lease.name, "status": "Invoiced"},
			fields=["name", "sales_invoice"], limit=1,
		)[0]
		frappe.get_doc("Sales Invoice", inv.sales_invoice).cancel()
		self.assertEqual(frappe.db.get_value("Charge Schedule", inv.name, "status"), "Planned")
		self.assertFalse(frappe.db.get_value("Charge Schedule", inv.name, "sales_invoice"))

	# ---- teardown helpers ----------------------------------------------------
	def _cancel(self, dt, name):
		try:
			d = frappe.get_doc(dt, name)
			if d.docstatus == 1:
				d.cancel()
		except Exception:
			pass

	def _cancel_lease(self, name):
		try:
			for si in frappe.get_all(
				"Charge Schedule", filters={"lease_contract": name, "sales_invoice": ["is", "set"]}, pluck="sales_invoice"
			):
				self._cancel("Sales Invoice", si)
			lease = frappe.get_doc("Lease Contract", name)
			if lease.docstatus == 1:
				lease.cancel()
		except Exception:
			pass
