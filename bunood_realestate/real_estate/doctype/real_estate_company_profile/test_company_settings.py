# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure-logic tests for the multi-company resolver (no DB) — the blueprint's ten cases.
The regression gate: with NO profiles, resolution must reproduce today's single-company
behavior field-for-field (the legacy branch)."""

import json
import os
import unittest

from bunood_realestate.real_estate.company_settings import (
	GLOBAL_FIELDS,
	PROFILE_FIELDS,
	collect_values,
	missing_fields,
	resolve_config,
)

SINGLE = {
	"company": "Alpha",
	"rent_income_account": "Rent - A",
	"default_rent_item": "RENT-A",
	"receivable_account": "AR - A",
	"auto_submit_invoices": 1,
	"invoice_lead_days": 5,
}
PROFILE_B = {
	"company": "Beta", "enabled": 1,
	"rent_income_account": "Rent - B", "default_rent_item": "RENT-B",
	"receivable_account": "AR - B",
}


class TestResolveConfig(unittest.TestCase):
	def test_legacy_parity_matching_company(self):
		cfg = resolve_config("Alpha", {}, SINGLE)
		self.assertEqual(cfg["rent_income_account"], "Rent - A")
		self.assertEqual(cfg["auto_submit_invoices"], 1)
		self.assertEqual(cfg["_source"], "settings")

	def test_legacy_parity_blank_single_company_serves_anyone(self):
		single = dict(SINGLE, company=None)
		cfg = resolve_config("Anything", {}, single)
		self.assertIsNotNone(cfg)
		self.assertEqual(cfg["default_rent_item"], "RENT-A")

	def test_mismatched_company_without_profile_fails_loud(self):
		self.assertIsNone(resolve_config("Beta", {}, SINGLE))

	def test_profile_beats_matching_single_whole_block(self):
		# Even for a field the profile leaves BLANK, the single's value must NOT bleed in.
		cfg = resolve_config("Beta", {"Beta": PROFILE_B}, SINGLE)
		self.assertEqual(cfg["rent_income_account"], "Rent - B")
		self.assertFalse(cfg.get("opening_balance_account"))  # blank stays blank
		self.assertEqual(cfg["_source"], "profile")

	def test_disabled_profile_is_terminal(self):
		"""'Park a company' = its documents fail loud — a disabled profile NEVER falls back
		to the Single, not even for the Single's own company (silent fallback would swap
		the payout discriminators with zero warning)."""
		disabled = dict(PROFILE_B, enabled=0)
		self.assertIsNone(resolve_config("Beta", {"Beta": disabled}, SINGLE))
		self.assertIsNone(
			resolve_config("Alpha", {"Alpha": dict(PROFILE_B, company="Alpha", enabled=0)}, SINGLE)
		)

	def test_global_fields_always_from_single(self):
		cfg = resolve_config("Beta", {"Beta": dict(PROFILE_B, auto_submit_invoices=0)}, SINGLE)
		self.assertEqual(cfg["auto_submit_invoices"], 1)  # profile's value ignored
		self.assertEqual(cfg["invoice_lead_days"], 5)

	def test_unknown_company_among_profiles_is_none(self):
		self.assertIsNone(resolve_config("Gamma", {"Beta": PROFILE_B}, SINGLE))

	def test_missing_fields_helper(self):
		cfg = resolve_config("Beta", {"Beta": PROFILE_B}, SINGLE)
		self.assertEqual(missing_fields(cfg, ["default_rent_item"]), [])
		self.assertEqual(
			missing_fields(cfg, ["opening_balance_account", "head_lease_item"]),
			["opening_balance_account", "head_lease_item"],
		)

	def test_collect_values_union_includes_disabled(self):
		vals = collect_values(
			"default_rent_item", SINGLE,
			[PROFILE_B, {"company": "Gamma", "enabled": 0, "default_rent_item": "RENT-G"}],
		)
		self.assertEqual(vals, {"RENT-A", "RENT-B", "RENT-G"})

	def test_registry_matches_doctype_jsons(self):
		"""Drift guard: profile JSON fieldnames − {company, enabled, layout} == PROFILE_FIELDS,
		and every PROFILE/GLOBAL field exists on the Settings JSON."""
		here = os.path.dirname(os.path.abspath(__file__))
		profile = json.load(open(os.path.join(here, "real_estate_company_profile.json"), encoding="utf-8"))
		profile_fields = {
			f["fieldname"] for f in profile["fields"]
			if f["fieldtype"] not in ("Section Break", "Column Break", "HTML")
		} - {"company", "enabled"}
		self.assertEqual(profile_fields, set(PROFILE_FIELDS))

		settings_path = os.path.join(
			here, "..", "real_estate_settings", "real_estate_settings.json"
		)
		settings = json.load(open(settings_path, encoding="utf-8"))
		settings_fields = {f["fieldname"] for f in settings["fields"]}
		for f in PROFILE_FIELDS + GLOBAL_FIELDS:
			self.assertIn(f, settings_fields, f"{f} missing from Real Estate Settings")


if __name__ == "__main__":
	unittest.main()
