# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure-logic unit tests for the Charge Engine (no DB) — the money/date/grouping algorithms
that must never drift. Run:  bench --site <site> run-tests --app bunood_realestate"""

import datetime
import unittest

from bunood_realestate.real_estate.charge_engine import (
	charge_due_date,
	compute_consumption,
	group_key,
	partition,
)


def _row(lc="LC-1", cat="Utility", tax="T15", due="2026-01-01", cust="CUST", comp="CO"):
	return {
		"lease_charge_row": lc, "category": cat, "tax_template": tax,
		"due_date": due, "customer": cust, "company": comp,
	}


class TestChargeDueDate(unittest.TestCase):
	def test_advance_is_period_start(self):
		self.assertEqual(charge_due_date("2026-03-01", "2026-03-31", "Advance"), datetime.date(2026, 3, 1))

	def test_arrears_is_period_end(self):
		self.assertEqual(charge_due_date("2026-03-01", "2026-03-31", "Arrears"), datetime.date(2026, 3, 31))


class TestGrouping(unittest.TestCase):
	def test_separate_one_bucket_per_charge(self):
		rows = [_row(lc="A"), _row(lc="B"), _row(lc="A")]
		buckets = partition(rows, "separate")
		self.assertEqual(len(buckets), 2)  # A (2 rows) + B (1 row)
		self.assertEqual({len(b) for b in buckets}, {2, 1})

	def test_group_by_category(self):
		rows = [_row(lc="A", cat="Utility"), _row(lc="B", cat="Utility"), _row(lc="C", cat="Service")]
		buckets = partition(rows, "group_by_category")
		self.assertEqual(len(buckets), 2)  # Utility (2) + Service (1)

	def test_single_one_bucket(self):
		rows = [_row(lc="A"), _row(lc="B"), _row(lc="C")]
		self.assertEqual(len(partition(rows, "single")), 1)

	def test_tax_template_always_splits(self):
		# Even under 'single', two different tax templates must never share an invoice.
		rows = [_row(lc="A", tax="T15"), _row(lc="B", tax="EXEMPT")]
		self.assertEqual(len(partition(rows, "single")), 2)

	def test_due_date_always_splits(self):
		rows = [_row(lc="A", due="2026-01-01"), _row(lc="A", due="2026-02-01")]
		self.assertEqual(len(partition(rows, "single")), 2)


class TestConsumption(unittest.TestCase):
	def test_normal(self):
		self.assertEqual(compute_consumption(100, 130), 30)

	def test_meter_replaced_adds_old_final(self):
		# New meter reads 20; old meter's final was 500; previous baseline 480 → 20+500-480 = 40.
		self.assertEqual(compute_consumption(480, 20, meter_replaced=True, replaced_meter_final=500), 40)

	def test_negative_raises(self):
		with self.assertRaises(Exception):
			compute_consumption(130, 100)


if __name__ == "__main__":
	unittest.main()
