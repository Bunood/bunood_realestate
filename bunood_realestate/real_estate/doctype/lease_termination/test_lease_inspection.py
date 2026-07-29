# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Move-out inspection → deductions pull (needs a submitted lease that still holds a
deposit). Verifies the single-path feed + idempotency."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from bunood_realestate.real_estate.doctype.lease_termination.lease_termination import pull_inspection_charges


class TestMoveOutInspection(FrappeTestCase):
	def test_pull_is_idempotent_and_feeds_deductions(self):
		lease = frappe.get_all(
			"Lease Contract",
			filters={"docstatus": 1, "deposit_received": [">", 0]},
			fields=["name", "company", "deposit_received", "deposit_refunded"],
			limit=1,
		)
		if not lease:
			self.skipTest("No submitted lease still holding a deposit")
		lease = lease[0]
		held = flt(lease.deposit_received) - flt(lease.deposit_refunded)
		if held <= 0:
			self.skipTest("Deposit already fully refunded")
		inc = frappe.get_all("Account", filters={"company": lease.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1)
		cash = frappe.get_all("Account", filters={"company": lease.company, "account_type": ["in", ["Bank", "Cash"]], "is_group": 0}, pluck="name", limit=1)
		if not (inc and cash):
			self.skipTest("Company missing income / cash account")

		settings = frappe.get_single("Real Estate Settings")
		settings.deduction_income_account = inc[0]
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)

		# Charge the full held deposit → net refund 0 (keeps validate happy without a refund).
		term = frappe.get_doc({
			"doctype": "Lease Termination", "lease_contract": lease.name,
			"termination_date": "2026-07-27", "refund_account": cash[0],
			"inspection": [
				{"area": "Walls", "condition": "Damaged", "charge": held},
				{"area": "Floors", "condition": "Good", "charge": 0},
			],
		})
		term.flags.ignore_permissions = True
		term.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("Lease Termination", term.name, force=True, ignore_permissions=True))

		res = pull_inspection_charges(term.name)
		self.assertEqual(res["added"], 1, "only the charged line is pulled")
		term.reload()
		self.assertEqual(len(term.deductions), 1)
		self.assertEqual(flt(term.deductions[0].amount), flt(held))
		self.assertEqual(term.deductions[0].income_account, inc[0])
		self.assertTrue(term.inspection[0].pulled)

		# Re-run: the pulled line is skipped → nothing new (no double-charge).
		self.assertEqual(pull_inspection_charges(term.name)["added"], 0)
		term.reload()
		self.assertEqual(len(term.deductions), 1)
