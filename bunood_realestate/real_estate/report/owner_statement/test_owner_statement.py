# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Test the Owner Statement report aggregates posted Owner Payout records correctly."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from bunood_realestate.real_estate.report.owner_statement.owner_statement import execute


class TestOwnerStatement(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		sg = frappe.get_all("Supplier Group", pluck="name", limit=1)
		if not sg:
			self.skipTest("No Supplier Group")
		self.owner = "Bunood Statement Owner"
		if not frappe.db.exists("Supplier", self.owner):
			frappe.get_doc({"doctype": "Supplier", "supplier_name": self.owner, "supplier_group": sg[0]}).insert(ignore_permissions=True)
		self.prop = frappe.db.get_value("Property", {"property_name": "OwnerStmt Property"})
		if not self.prop:
			bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name") or frappe.get_doc(
				{"doctype": "RE Business Type", "title": "Exempt-Stmt", "vat_treatment": "Exempt"}
			).insert(ignore_permissions=True).name
			self.prop = frappe.get_doc({
				"doctype": "Property", "property_name": "OwnerStmt Property",
				"company": self.company, "business_type": bt, "owner_party": self.owner,
			}).insert(ignore_permissions=True).name
		self._payouts = []
		for frm, to, rent, fee_amt, net in [
			("2026-01-01", "2026-01-31", 10000, 1000, 9000),
			("2026-02-01", "2026-02-28", 12000, 1200, 10800),
		]:
			d = frappe.get_doc({
				"doctype": "Owner Payout", "property": self.prop, "owner_party": self.owner,
				"company": self.company, "from_date": frm, "to_date": to,
				"rent_base": rent, "fee_percentage": 10, "fee_amount": fee_amt,
				"owner_payout": net, "status": "Posted",
			}).insert(ignore_permissions=True)
			self._payouts.append(d.name)
			self.addCleanup(lambda n=d.name: frappe.delete_doc("Owner Payout", n, force=True, ignore_permissions=True))

	def test_aggregates_posted_payouts_with_totals(self):
		columns, rows, _a, _b, summary = execute({"owner": self.owner, "company": self.company})
		mine = [r for r in rows if r["property"] == self.prop]
		self.assertEqual(len(mine), 2, "both posted payouts should appear")
		self.assertEqual(sorted(flt(r["owner_payout"]) for r in mine), [9000.0, 10800.0])
		# Summary totals across the two periods.
		by_label = {s["label"]: s["value"] for s in summary}
		self.assertEqual(by_label["Rent Base"], 22000.0)
		self.assertEqual(by_label["Management Fee"], 2200.0)
		self.assertEqual(by_label["Net Paid to Owner"], 19800.0)

	def test_period_filter_excludes_outside_window(self):
		# Only January overlaps this window → one row.
		_c, rows, _a, _b, _s = execute(
			{"owner": self.owner, "company": self.company, "from_date": "2026-01-01", "to_date": "2026-01-15"}
		)
		mine = [r for r in rows if r["property"] == self.prop]
		self.assertEqual(len(mine), 1)
		self.assertEqual(flt(mine[0]["owner_payout"]), 9000.0)

	def test_cross_company_filter_is_rejected(self):
		self.assertRaises(
			frappe.PermissionError, execute, {"owner": self.owner, "company": "No Such Co ZZZ"}
		)

	def test_no_owner_no_property_returns_empty(self):
		_c, rows, _a, _b, _s = execute({})
		self.assertEqual(rows, [])
