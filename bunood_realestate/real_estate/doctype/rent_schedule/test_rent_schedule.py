# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure-logic unit tests (no DB) — run with:  bench --site <site> run-tests --app bunood_realestate
Covers the money/date algorithms that must never drift."""

import unittest

from bunood_realestate.real_estate.doctype.lease_contract.lease_contract import ZATCA_VAT_RE
from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import build_periods
from bunood_realestate.real_estate.management import compute_owner_payout
from bunood_realestate.real_estate.tasks import split_amount


def _total(periods):
	return round(sum(p["base_amount"] for p in periods), 2)


class TestBuildPeriods(unittest.TestCase):
	def test_year_monthly(self):
		ps = build_periods("2026-01-01", "2026-12-31", "Monthly", 120000)
		self.assertEqual(len(ps), 12)
		self.assertEqual(_total(ps), 120000)

	def test_year_quarterly(self):
		self.assertEqual(len(build_periods("2026-01-01", "2026-12-31", "Quarterly", 120000)), 4)

	def test_year_annual_single(self):
		self.assertEqual(len(build_periods("2026-01-01", "2026-12-31", "Annual", 120000)), 1)

	def test_three_months(self):
		ps = build_periods("2026-01-01", "2026-03-31", "Monthly", 120000)
		self.assertEqual(len(ps), 3)
		self.assertEqual(_total(ps), 30000)

	def test_partial_last_period_prorated(self):
		ps = build_periods("2026-01-01", "2026-06-15", "Monthly", 120000)
		self.assertEqual(len(ps), 6)
		self.assertEqual(_total(ps), 55000)
		self.assertTrue(ps[-1]["is_prorated"])

	def test_leap_feb29_annual_no_residual(self):
		ps = build_periods("2024-02-29", "2025-02-28", "Annual", 120000)
		self.assertEqual(len(ps), 1)
		self.assertEqual(_total(ps), 120000)

	def test_month_end_no_drift(self):
		ps = build_periods("2026-01-31", "2027-01-30", "Monthly", 120000)
		self.assertEqual(len(ps), 12)
		self.assertEqual(_total(ps), 120000)

	def test_non_divisible_total_exact(self):
		ps = build_periods("2026-01-01", "2026-12-31", "Monthly", 100000)
		self.assertEqual(len(ps), 12)
		self.assertEqual(_total(ps), 100000)


class TestSplitAmount(unittest.TestCase):
	def test_two_units(self):
		self.assertEqual(split_amount(5000, [10000, 20000]), [1666.67, 3333.33])

	def test_sum_is_exact(self):
		self.assertAlmostEqual(sum(split_amount(10000, [1, 1, 1])), 10000, places=2)

	def test_zero_base(self):
		self.assertEqual(split_amount(0, [1, 1]), [0.0, 0.0])


class TestOwnerPayout(unittest.TestCase):
	def test_managed_ten_percent(self):
		r = compute_owner_payout(10000, 10)
		self.assertEqual(r["fee"], 1000)
		self.assertEqual(r["owner_payout"], 9000)

	def test_zero_fee_all_to_owner(self):
		self.assertEqual(compute_owner_payout(10000, 0)["owner_payout"], 10000)


class TestZatcaVatRegex(unittest.TestCase):
	def test_valid(self):
		self.assertTrue(ZATCA_VAT_RE.match("300000000000003"))

	def test_invalid_short(self):
		self.assertFalse(ZATCA_VAT_RE.match("123"))

	def test_invalid_boundaries(self):
		self.assertFalse(ZATCA_VAT_RE.match("100000000000001"))
