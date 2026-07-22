# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test for the owner-payout idempotency guard (needs a configured site).
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.owner_payout.test_owner_payout"""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.management import compute_owner_payout, generate_owner_payout


class TestOwnerPayoutPure(FrappeTestCase):
	def test_compute_split(self):
		self.assertEqual(compute_owner_payout(10000, 10), {"rent_base": 10000.0, "fee": 1000.0, "owner_payout": 9000.0})
		self.assertEqual(compute_owner_payout(10000, 0)["owner_payout"], 10000.0)


class TestOwnerPayoutIdempotency(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured on this site")
		self.company = companies[0]
		if not frappe.db.get_value("Company", self.company, "default_payable_account"):
			self.skipTest("Company has no Default Payable Account")

		self.model = frappe.db.get_value("RE Management Model", {"behavior": "managed"}, "name")
		if not self.model:
			self.model = frappe.get_doc({
				"doctype": "RE Management Model", "title": "Managed-Test", "behavior": "managed"
			}).insert(ignore_permissions=True).name

		expense = frappe.get_all(
			"Account",
			filters={"company": self.company, "root_type": "Expense", "is_group": 0},
			pluck="name", limit=1,
		)
		if not expense:
			self.skipTest("No expense account on the company")
		settings = frappe.get_single("Real Estate Settings")
		settings.owner_payout_expense_account = expense[0]
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)

		self.owner = frappe.get_doc({
			"doctype": "Supplier", "supplier_name": "Bunood Test Owner", "supplier_group": frappe.get_all("Supplier Group", pluck="name", limit=1)[0]
		}).insert(ignore_permissions=True).name if not frappe.db.exists("Supplier", "Bunood Test Owner") else "Bunood Test Owner"

		self.prop = frappe.get_doc({
			"doctype": "Property",
			"property_name": "Bunood Payout Test Property",
			"company": self.company,
			"business_type": self._business_type(),
			"management_model": self.model,
			"owner_party": self.owner,
			"management_fee_percentage": 10,
		}).insert(ignore_permissions=True).name

	def _business_type(self):
		name = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name")
		if name:
			return name
		return frappe.get_doc({
			"doctype": "RE Business Type", "title": "Exempt-Test", "vat_treatment": "Exempt"
		}).insert(ignore_permissions=True).name

	def test_overlapping_window_is_rejected(self):
		"""An already-Posted payout blocks any overlapping window — no double-pay."""
		frappe.get_doc({
			"doctype": "Owner Payout",
			"property": self.prop,
			"owner_party": self.owner,
			"company": self.company,
			"from_date": "2026-01-01",
			"to_date": "2026-01-31",
			"owner_payout": 9000,
			"status": "Posted",
		}).insert(ignore_permissions=True)

		# Jan 15 – Feb 15 overlaps the Jan payout → must raise before posting anything.
		self.assertRaises(
			frappe.ValidationError,
			generate_owner_payout,
			property=self.prop,
			from_date="2026-01-15",
			to_date="2026-02-15",
		)
