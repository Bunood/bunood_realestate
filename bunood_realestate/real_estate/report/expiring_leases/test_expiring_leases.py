# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Tests for the Expiring Leases report (company scoping + shape)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.report.expiring_leases.expiring_leases import execute


class TestExpiringLeases(FrappeTestCase):
	def test_returns_columns_and_list(self):
		columns, data = execute({"days": 60})
		self.assertTrue(any(c["fieldname"] == "days_left" for c in columns))
		self.assertIsInstance(data, list)
		# Every row is within the window and carries the derived fields.
		for r in data:
			self.assertIn("renewal_drafted", r)
			self.assertLessEqual(r["days_left"], 60)

	def test_cross_company_filter_is_rejected(self):
		self.assertRaises(frappe.PermissionError, execute, {"company": "No Such Co ZZZ"})
