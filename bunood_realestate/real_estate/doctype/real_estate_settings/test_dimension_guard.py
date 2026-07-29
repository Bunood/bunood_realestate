# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure tests for the Phase-0 dimension guard (plan-financial-reporting.md).
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.real_estate_settings.test_dimension_guard"""

from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.dimension_guard import check_rows

RE_ACCOUNTS = {"Rent Income - B", "Maintenance Expense - B"}


class TestDimensionGuardPure(FrappeTestCase):
	def test_non_re_row_is_ignored(self):
		rows = [{"idx": 1, "account": "Office Supplies - B", "property": None, "real_estate_unit": None}]
		self.assertEqual(check_rows(rows, RE_ACCOUNTS, {}), [])

	def test_re_account_without_property_is_flagged(self):
		rows = [{"idx": 1, "account": "Rent Income - B", "property": None, "real_estate_unit": None}]
		problems = check_rows(rows, RE_ACCOUNTS, {})
		self.assertEqual(len(problems), 1)
		self.assertIn("Rent Income - B", problems[0])

	def test_re_account_with_property_passes(self):
		rows = [{"idx": 1, "account": "Rent Income - B", "property": "PROP-001", "real_estate_unit": None}]
		self.assertEqual(check_rows(rows, RE_ACCOUNTS, {}), [])

	def test_unit_only_row_is_re_and_needs_property(self):
		# A unit tag alone marks the row as real estate — property becomes mandatory.
		rows = [{"idx": 2, "account": "Office Supplies - B", "property": None, "real_estate_unit": "U-9"}]
		problems = check_rows(rows, RE_ACCOUNTS, {"U-9": "PROP-001"})
		self.assertEqual(len(problems), 1)

	def test_unit_property_mismatch_is_flagged(self):
		rows = [{"idx": 3, "account": "Rent Income - B", "property": "PROP-002", "real_estate_unit": "U-9"}]
		problems = check_rows(rows, RE_ACCOUNTS, {"U-9": "PROP-001"})
		self.assertEqual(len(problems), 1)
		self.assertIn("U-9", problems[0])

	def test_unit_property_match_passes(self):
		rows = [{"idx": 3, "account": "Rent Income - B", "property": "PROP-001", "real_estate_unit": "U-9"}]
		self.assertEqual(check_rows(rows, RE_ACCOUNTS, {"U-9": "PROP-001"}), [])

	def test_multiple_rows_accumulate_problems(self):
		rows = [
			{"idx": 1, "account": "Rent Income - B", "property": None, "real_estate_unit": None},
			{"idx": 2, "account": "Maintenance Expense - B", "property": None, "real_estate_unit": None},
			{"idx": 3, "account": "Office Supplies - B", "property": None, "real_estate_unit": None},
		]
		self.assertEqual(len(check_rows(rows, RE_ACCOUNTS, {})), 2)
