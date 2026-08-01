# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure-logic tests for CAM / service-charge apportionment (no DB).

The invariants that must hold for every pool, weighting basis, and vacant policy:
  * billed shares sum EXACTLY to billed_total (no per-unit rounding drift);
  * pool == billed_total + owner_share (nothing created or lost);
  * Owner-Absorbs bills only the occupied fair share (vacant slice → owner);
  * Redistribute bills the whole pool across occupied units;
  * all-vacant → owner keeps the whole pool, nobody is billed.
"""

import unittest

from bunood_realestate.real_estate.apportion import split_amount
from bunood_realestate.real_estate.cam import (
	apportion_cam,
	cam_billed_total,
	_unit_weight,
)


def _units(*specs):
	"""specs: (unit, weight, billable) → the shape apportion_cam consumes."""
	return [
		{"unit": u, "weight": w, "billable": b, "lease_contract": (u + "-L") if b else None,
		 "customer": (u + "-C") if b else None}
		for (u, w, b) in specs
	]


class TestSplitAmount(unittest.TestCase):
	def test_last_item_absorbs_remainder(self):
		# 100 / 3 equal → 33.33, 33.33, 33.34 ; sums EXACTLY to 100.
		shares = split_amount(100, [1, 1, 1])
		self.assertEqual(shares, [33.33, 33.33, 33.34])
		self.assertEqual(round(sum(shares), 2), 100.0)

	def test_zero_total_weights_even_split(self):
		# All-zero weights must not divide by zero — fall back to an even split.
		shares = split_amount(90, [0, 0, 0])
		self.assertEqual(round(sum(shares), 2), 90.0)

	def test_empty(self):
		self.assertEqual(split_amount(100, []), [])


class TestCamBilledTotal(unittest.TestCase):
	def test_owner_absorbs_scales_by_billable_share(self):
		# pool 1200, half the weight occupied → owner-absorbs bills 600.
		self.assertEqual(cam_billed_total(1200, 100, 50, True), 600.0)

	def test_redistribute_bills_whole_pool(self):
		self.assertEqual(cam_billed_total(1200, 100, 50, False), 1200.0)

	def test_zero_total_weight_bills_nothing(self):
		# No rentable weight at all (property empty / all excluded) → bill nothing.
		self.assertEqual(cam_billed_total(1200, 0, 0, True), 0.0)
		self.assertEqual(cam_billed_total(1200, 0, 0, False), 0.0)


class TestApportionCam(unittest.TestCase):
	def _assert_conserved(self, pool, result):
		billed = round(sum(s["share"] for s in result["shares"]), 2)
		self.assertEqual(billed, round(result["billed_total"], 2))
		self.assertEqual(round(result["billed_total"] + result["owner_share"], 2), round(pool, 2))

	def test_full_occupancy_area_basis(self):
		# 3 units, areas 100/200/300, all occupied, owner-absorbs → whole pool billed.
		r = apportion_cam(600, _units(("A", 100, True), ("B", 200, True), ("C", 300, True)))
		self.assertEqual(r["billed_total"], 600.0)
		self.assertEqual(r["owner_share"], 0.0)
		self.assertEqual([s["share"] for s in r["shares"]], [100.0, 200.0, 300.0])
		self._assert_conserved(600, r)

	def test_owner_absorbs_vacant_slice(self):
		# unit C (weight 300 of 600) vacant, owner-absorbs → bill 600 * 300/600 = 300.
		r = apportion_cam(600, _units(("A", 100, True), ("B", 200, True), ("C", 300, False)))
		self.assertEqual(r["billed_total"], 300.0)
		self.assertEqual(r["owner_share"], 300.0)
		self.assertEqual({s["unit"] for s in r["shares"]}, {"A", "B"})
		self._assert_conserved(600, r)

	def test_redistribute_to_occupied(self):
		# Same vacancy, redistribute → occupied A+B split the WHOLE 600 by their weights.
		r = apportion_cam(
			600,
			_units(("A", 100, True), ("B", 200, True), ("C", 300, False)),
			owner_absorbs=False,
		)
		self.assertEqual(r["billed_total"], 600.0)
		self.assertEqual(r["owner_share"], 0.0)
		# A:B = 100:200 of 600 → 200 / 400.
		by = {s["unit"]: s["share"] for s in r["shares"]}
		self.assertEqual(by, {"A": 200.0, "B": 400.0})
		self._assert_conserved(600, r)

	def test_all_vacant_owner_keeps_pool(self):
		for absorbs in (True, False):
			r = apportion_cam(600, _units(("A", 100, False), ("B", 200, False)), owner_absorbs=absorbs)
			self.assertEqual(r["shares"], [])
			self.assertEqual(r["billed_total"], 0.0)
			self.assertEqual(r["owner_share"], 600.0)

	def test_equal_basis_indivisible_pool(self):
		# 100 across 3 equal occupied units → 33.33/33.33/33.34, sums to 100.
		r = apportion_cam(100, _units(("A", 1, True), ("B", 1, True), ("C", 1, True)))
		self.assertEqual(sorted(s["share"] for s in r["shares"]), [33.33, 33.33, 33.34])
		self._assert_conserved(100, r)

	def test_zero_weight_unit_excluded_from_billing(self):
		# A billable but weight 0 (e.g. Rent-Share on a peppercorn lease) → not billed.
		r = apportion_cam(300, _units(("A", 0, True), ("B", 100, True)))
		self.assertEqual({s["unit"] for s in r["shares"]}, {"B"})
		self._assert_conserved(300, r)

	def test_fuzz_conservation_all_shapes(self):
		# Deterministic pseudo-random weights/occupancy; the two invariants must always hold.
		state = 20260731
		def nxt(mod):
			nonlocal state
			state = (1103515245 * state + 12345) & 0x7FFFFFFF
			return state % mod
		for _ in range(400):
			n = 1 + nxt(6)
			specs = [(f"U{i}", nxt(500), bool(nxt(2))) for i in range(n)]
			pool = 1 + nxt(100000) / 100.0
			for absorbs in (True, False):
				r = apportion_cam(pool, _units(*specs), owner_absorbs=absorbs)
				billed = round(sum(s["share"] for s in r["shares"]), 2)
				self.assertEqual(billed, round(r["billed_total"], 2))
				self.assertEqual(round(r["billed_total"] + r["owner_share"], 2), round(pool, 2))
				# Never bill more than the pool.
				self.assertLessEqual(r["billed_total"], round(pool, 2) + 0.005)


class TestUnitWeight(unittest.TestCase):
	def test_bases(self):
		row = {"area_sqm": 120, "cam_weight": 3.5, "annual_rent": 60000}
		self.assertEqual(_unit_weight("Area", row), 120.0)
		self.assertEqual(_unit_weight("Equal", row), 1.0)
		self.assertEqual(_unit_weight("Custom Weight", row), 3.5)
		self.assertEqual(_unit_weight("Rent Share", row), 60000.0)

	def test_missing_values_default_zero(self):
		self.assertEqual(_unit_weight("Area", {}), 0.0)
		self.assertEqual(_unit_weight("Custom Weight", {}), 0.0)
		self.assertEqual(_unit_weight("Rent Share", {}), 0.0)
		# Equal is always 1 regardless of the row.
		self.assertEqual(_unit_weight("Equal", {}), 1.0)

	def test_negative_weight_clamped_to_zero(self):
		# A negative area / cam_weight must never push the billable ratio above 1 (which would
		# over-recover the pool) — it is clamped to 0 so bad input contributes nothing.
		self.assertEqual(_unit_weight("Area", {"area_sqm": -100}), 0.0)
		self.assertEqual(_unit_weight("Custom Weight", {"cam_weight": -5}), 0.0)
		self.assertEqual(_unit_weight("Rent Share", {"annual_rent": -1}), 0.0)

	def test_negative_weight_cannot_overbill_pool(self):
		# End-to-end: a vacant unit with a (clamped) negative weight must not make Owner-Absorbs
		# bill more than the pool. Weights come through _unit_weight, so build rows that way.
		occupied = {"unit": "A", "weight": _unit_weight("Custom Weight", {"cam_weight": 100}),
					"billable": True, "lease_contract": "A-L", "customer": "A-C"}
		vacant_neg = {"unit": "B", "weight": _unit_weight("Custom Weight", {"cam_weight": -50}),
					  "billable": False}
		r = apportion_cam(1200, [occupied, vacant_neg])
		self.assertLessEqual(r["billed_total"], 1200.0)
		self.assertGreaterEqual(r["owner_share"], 0.0)
		self.assertEqual(round(r["billed_total"] + r["owner_share"], 2), 1200.0)


if __name__ == "__main__":
	unittest.main()
