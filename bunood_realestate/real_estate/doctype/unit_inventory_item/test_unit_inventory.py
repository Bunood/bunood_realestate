# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure-logic tests for the Unit Inventory layer (no DB): handover snapshot rows,
move-out checklist generation, and the computed readiness indicator."""

import unittest

from bunood_realestate.real_estate.doctype.lease_contract.lease_contract import snapshot_rows
from bunood_realestate.real_estate.doctype.lease_termination.lease_termination import (
	handover_checklist_rows,
)
from bunood_realestate.real_estate.readiness import compute_readiness


class TestSnapshotRows(unittest.TestCase):
	def test_copies_values_not_references(self):
		rows = snapshot_rows([
			{"unit": "U-205", "item_type": "مكيف", "qty": 3, "brand": "Gree", "condition": "ممتاز"},
			{"unit": "U-205", "item_type": "ثلاجة", "qty": 1, "brand": "LG", "condition": "جيد"},
		])
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0], {
			"item_label": "مكيف", "qty": 3, "brand": "Gree",
			"condition": "ممتاز", "source_unit": "U-205",
		})

	def test_skips_zero_qty_and_handles_missing_fields(self):
		rows = snapshot_rows([
			{"unit": "U-1", "item_type": "فرن", "qty": 0},
			{"unit": "U-1", "item_type": "سرير", "qty": 2},
			{"unit": "U-1", "item_type": None, "qty": 1, "brand": None, "condition": None},
		])
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["item_label"], "سرير")
		self.assertEqual(rows[1]["item_label"], "")  # tolerated, empty label

	def test_empty_inventory_gives_empty_snapshot(self):
		self.assertEqual(snapshot_rows([]), [])
		self.assertEqual(snapshot_rows(None), [])


class TestHandoverChecklist(unittest.TestCase):
	def test_one_checklist_row_per_snapshot_line(self):
		rows = handover_checklist_rows([
			{"item_label": "مكيف", "qty": 3, "brand": "Gree", "condition": "ممتاز"},
			{"item_label": "ثلاجة", "qty": 1, "brand": "", "condition": ""},
		])
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["area"], "Inventory")
		self.assertEqual(rows[0]["condition"], "Good")  # inspector flips to Damaged/Missing
		self.assertEqual(rows[0]["charge"], 0)  # only priced items feed the deductions
		self.assertIn("3 × مكيف (Gree)", rows[0]["note"])
		self.assertIn("ممتاز", rows[0]["note"])
		self.assertEqual(rows[1]["note"], "1 × ثلاجة")

	def test_skips_blank_and_zero_qty_lines(self):
		rows = handover_checklist_rows([
			{"item_label": "", "qty": 1},
			{"item_label": "سرير", "qty": 0},
			{"item_label": "كنب", "qty": 1},
		])
		self.assertEqual(len(rows), 1)
		self.assertIn("كنب", rows[0]["note"])

	def test_empty_snapshot_gives_empty_checklist(self):
		self.assertEqual(handover_checklist_rows([]), [])
		self.assertEqual(handover_checklist_rows(None), [])

	def test_multi_unit_lease_tags_the_unit_in_notes(self):
		# Identical items in DIFFERENT units must stay distinguishable at move-out.
		rows = handover_checklist_rows([
			{"item_label": "مكيف", "qty": 1, "source_unit": "U-101"},
			{"item_label": "مكيف", "qty": 1, "source_unit": "U-102"},
		])
		self.assertIn("U-101", rows[0]["note"])
		self.assertIn("U-102", rows[1]["note"])

	def test_single_unit_lease_keeps_notes_clean(self):
		rows = handover_checklist_rows([
			{"item_label": "مكيف", "qty": 1, "source_unit": "U-101"},
			{"item_label": "ثلاجة", "qty": 1, "source_unit": "U-101"},
		])
		self.assertNotIn("U-101", rows[0]["note"])  # no noise when there is only one unit


class TestReadiness(unittest.TestCase):
	def test_all_signals_on_is_100(self):
		r = compute_readiness({"pricing": True, "meters": True, "inventory": True, "photos": True})
		self.assertEqual(r["pct"], 100)
		self.assertEqual(r["missing"], [])

	def test_partial_signals(self):
		r = compute_readiness({"pricing": True, "meters": True, "inventory": False, "photos": False})
		self.assertEqual(r["pct"], 50)
		self.assertEqual([m["key"] for m in r["missing"]], ["inventory", "photos"])

	def test_no_signals_is_0(self):
		r = compute_readiness({})
		self.assertEqual(r["pct"], 0)
		self.assertEqual(len(r["missing"]), 4)


if __name__ == "__main__":
	unittest.main()
