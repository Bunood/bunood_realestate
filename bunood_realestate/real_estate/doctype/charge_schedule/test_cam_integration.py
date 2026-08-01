# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration tests for the CAM materializer (needs a configured site).
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.charge_schedule.test_cam_integration

These exercise the DB path the pure test_cam.py cannot: live occupancy resolution, the
period-level idempotency guard, and that CAM rows are billed by the EXISTING generator as
native Sales Invoices (never as rent). Money math itself is covered exhaustively in test_cam.py.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, get_first_day, get_last_day, getdate, nowdate

from bunood_realestate.real_estate.cam import generate_cam_schedule


class TestCamIntegration(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated")
		if not frappe.get_meta("Charge Schedule").has_field("is_cam"):
			self.skipTest("CAM fields not migrated")
		income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		if not income:
			self.skipTest("No income account")
		self.income = income[0]

		# A dedicated CAM Charge Type carrying a Service Item — NOT a rent item.
		item = "Bunood CAM Test Service Item"
		if not frappe.db.exists("Item", item):
			doc = frappe.get_doc({
				"doctype": "Item", "item_code": item, "item_name": item, "is_stock_item": 0,
				"item_group": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
			})
			doc.append("item_defaults", {"company": self.company, "income_account": self.income})
			doc.insert(ignore_permissions=True)
		self.charge_type = "Bunood CAM Cleaning"
		if not frappe.db.exists("Charge Type", self.charge_type):
			frappe.get_doc({
				"doctype": "Charge Type", "charge_type_name": self.charge_type,
				"charge_kind": "Service", "item": item, "is_active": 1, "is_recurring": 1,
			}).insert(ignore_permissions=True)

		# One CAM period entirely in the PREVIOUS calendar month, so Arrears due date (= period
		# end) is already <= today on any server date. The lease spans a wide active window.
		self.period_start = get_first_day(add_months(getdate(nowdate()), -1))
		self.period_end = get_last_day(add_months(getdate(nowdate()), -1))

		self.prop = self._property()
		self.unit_a = self._unit("CAM-UA", area=100)   # occupied
		self.unit_b = self._unit("CAM-UB", area=300)   # vacant
		self.customer = self._customer()
		self.lease = self._active_lease(self.unit_a)

	# ---- fixtures ----------------------------------------------------------------
	def _property(self):
		bt = frappe.db.get_value("RE Business Type", {}, "name") or frappe.get_doc(
			{"doctype": "RE Business Type", "title": "CAM-BT", "vat_treatment": "Exempt"}
		).insert(ignore_permissions=True).name
		name = frappe.get_doc({
			"doctype": "Property", "property_name": "Bunood CAM Property",
			"company": self.company, "business_type": bt,
		}).insert(ignore_permissions=True).name
		self.addCleanup(self._delete, "Property", name)
		return name

	def _unit(self, number, area):
		name = frappe.get_doc({
			"doctype": "Real Estate Unit", "unit_number": number, "property": self.prop,
			"status": "Vacant", "area_sqm": area,
		}).insert(ignore_permissions=True).name
		self.addCleanup(self._delete, "Real Estate Unit", name)
		return name

	def _customer(self):
		name = "Bunood CAM Tenant"
		if not frappe.db.exists("Customer", name):
			frappe.get_doc({
				"doctype": "Customer", "customer_name": name,
				"customer_group": frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
				"territory": frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		return name

	def _active_lease(self, unit):
		lease = frappe.get_doc({
			"doctype": "Lease Contract",
			"customer": self.customer, "property": self.prop, "company": self.company,
			"contract_type": "Residential", "billing_cycle": "Monthly",
			"start_date": add_months(self.period_start, -3),
			"end_date": add_months(self.period_end, 6),
			"annual_rent_total": 120000,
			"units": [{"unit": unit, "annual_rent": 120000}],
		})
		lease.flags.ignore_permissions = True
		lease.insert(ignore_permissions=True)
		lease.submit()
		self.addCleanup(self._cancel, "Lease Contract", lease.name)
		return lease

	def _add_service_charge(self, basis="Area", policy="Owner Absorbs", pool=400):
		prop = frappe.get_doc("Property", self.prop)
		prop.set("service_charges", [])
		prop.append("service_charges", {
			"charge_type": self.charge_type, "pool_amount": pool, "allocation_basis": basis,
			"billing_cycle": "Monthly", "billing_timing": "Arrears", "vacant_policy": policy,
			"charge_start_date": self.period_start, "charge_end_date": self.period_end,
			"is_active": 1, "revenue_account": self.income,
		})
		prop.flags.ignore_permissions = True
		prop.save(ignore_permissions=True)

	def _cam_rows(self):
		return frappe.get_all(
			"Charge Schedule",
			filters={"property": self.prop, "is_cam": 1},
			fields=["name", "unit", "base_amount", "status", "billing_method", "cam_pool", "customer"],
		)

	# ---- tests -------------------------------------------------------------------
	def test_owner_absorbs_bills_only_occupied_fair_share(self):
		self._add_service_charge(basis="Area", policy="Owner Absorbs", pool=400)
		generate_cam_schedule(property=self.prop)
		rows = self._cam_rows()
		self.assertEqual(len(rows), 1, "only the occupied unit is billed under Owner-Absorbs")
		row = rows[0]
		self.assertEqual(row.unit, self.unit_a)
		self.assertEqual(row.customer, self.customer)
		self.assertEqual(row.status, "Planned")
		self.assertEqual(row.billing_method, "Fixed")
		# pool 400 * area 100/400 = 100 (unit B's 300/400 slice stays with the owner).
		self.assertEqual(row.base_amount, 100.0)

	def test_redistribute_bills_whole_pool_to_occupied(self):
		self._add_service_charge(basis="Area", policy="Redistribute to Occupied", pool=400)
		generate_cam_schedule(property=self.prop)
		rows = self._cam_rows()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].base_amount, 400.0, "the only occupied unit carries the whole pool")

	def test_materialization_is_idempotent(self):
		self._add_service_charge(basis="Area", policy="Owner Absorbs", pool=400)
		generate_cam_schedule(property=self.prop)
		first = {r.name for r in self._cam_rows()}
		generate_cam_schedule(property=self.prop)          # second run, same day
		second = {r.name for r in self._cam_rows()}
		self.assertEqual(first, second, "a re-run must not create or replace CAM rows")

	def test_cam_row_bills_as_native_invoice_not_rent(self):
		from bunood_realestate.real_estate.charge_engine import generate_due_charge_invoices

		self._add_service_charge(basis="Area", policy="Owner Absorbs", pool=400)
		generate_cam_schedule(property=self.prop)
		generate_due_charge_invoices()
		row = self._cam_rows()[0]
		status, si = frappe.db.get_value("Charge Schedule", row.name, ["status", "sales_invoice"])
		self.assertEqual(status, "Invoiced")
		self.assertTrue(si)
		line = frappe.get_doc("Sales Invoice", si).items[0]
		self.assertNotEqual(line.item_code, self._rent_item(), "CAM must never bill under the rent item")
		self.assertEqual(line.get("property"), self.prop, "CAM line carries the property dimension")

	def _rent_item(self):
		return frappe.db.get_single_value("Real Estate Settings", "default_rent_item")

	# ---- teardown ----------------------------------------------------------------
	def _cancel(self, dt, name):
		try:
			d = frappe.get_doc(dt, name)
			if d.docstatus == 1:
				d.cancel()
		except Exception:
			pass

	def _delete(self, dt, name):
		try:
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
		except Exception:
			pass
