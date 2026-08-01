# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure-logic unit tests (no DB) — run with:  bench --site <site> run-tests --app bunood_realestate
Covers the money/date algorithms that must never drift."""

import unittest

from bunood_realestate.real_estate.doctype.lease_contract.lease_contract import ZATCA_VAT_RE, lease_is_expired
from bunood_realestate.real_estate.doctype.lease_termination.lease_termination import unused_rent_credit
from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import (
	build_escalated_periods,
	build_periods,
	escalation_segments,
	seed_future_periods,
)
from bunood_realestate.real_estate.collections import compute_late_fee
from bunood_realestate.real_estate.management import compute_owner_payout
from bunood_realestate.real_estate.notifications import (
	current_milestone,
	document_reminder_detail,
	document_should_alert,
	document_status,
	expiry_milestone,
)
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


class TestLateFee(unittest.TestCase):
	def test_percentage(self):
		self.assertEqual(compute_late_fee(1000, "Percentage of Overdue", 2, 0), 20.0)

	def test_fixed(self):
		self.assertEqual(compute_late_fee(1000, "Fixed Amount", 50, 0), 50.0)

	def test_cap_applies(self):
		self.assertEqual(compute_late_fee(1000, "Percentage of Overdue", 5, 30), 30.0)

	def test_zero_when_not_overdue_or_no_value(self):
		self.assertEqual(compute_late_fee(0, "Percentage of Overdue", 5, 0), 0.0)
		self.assertEqual(compute_late_fee(1000, "Percentage of Overdue", 0, 0), 0.0)


class TestExpiryMilestone(unittest.TestCase):
	def test_hits_each_milestone(self):
		# today=2026-01-01, end at +60/+30/+7 → that milestone; else None.
		self.assertEqual(expiry_milestone("2025-01-01", "2026-03-02", "2026-01-01"), 60)
		self.assertEqual(expiry_milestone("2025-01-01", "2026-01-31", "2026-01-01"), 30)
		self.assertEqual(expiry_milestone("2025-01-01", "2026-01-08", "2026-01-01"), 7)

	def test_none_off_milestone(self):
		self.assertIsNone(expiry_milestone("2025-01-01", "2026-01-15", "2026-01-01"))  # 14 days
		self.assertIsNone(expiry_milestone("2025-01-01", "2025-12-31", "2026-01-01"))  # already past
		self.assertIsNone(expiry_milestone("2025-01-01", "2026-01-01", "2026-01-01"))  # today


class TestDocumentExpiry(unittest.TestCase):
	"""Pure core of Phase-1 #4 — the document-expiry milestone/predicate/key/status functions.
	The deed-never-expires regression (constraint #4) is executable here."""

	def test_current_milestone_buckets(self):
		# today = 2026-01-01. Exactly on a milestone boundary picks that milestone.
		self.assertEqual(current_milestone("2026-04-01", "2026-01-01"), 90)  # 90 days out
		self.assertEqual(current_milestone("2026-01-31", "2026-01-01"), 30)  # 30
		self.assertEqual(current_milestone("2026-01-08", "2026-01-01"), 7)   # 7
		self.assertEqual(current_milestone("2026-01-01", "2026-01-01"), 0)   # expires today

	def test_current_milestone_tightest_bucket_is_catch_up_safe(self):
		# 25 days out is inside the 30 window (not exactly 30) → still fires the 30 bucket, so a
		# missed scheduler day never permanently skips a milestone. Returns the TIGHTEST bucket.
		self.assertEqual(current_milestone("2026-01-26", "2026-01-01"), 30)  # 25 days → 30
		self.assertEqual(current_milestone("2026-01-06", "2026-01-01"), 7)   # 5 days → 7
		self.assertEqual(current_milestone("2026-02-15", "2026-01-01"), 90)  # 45 days → 90

	def test_current_milestone_outside_window_or_past(self):
		self.assertIsNone(current_milestone("2026-05-01", "2026-01-01"))  # 120 days → outside 90
		self.assertIsNone(current_milestone("2025-12-31", "2026-01-01"))  # already expired

	def test_should_alert_short_circuits(self):
		# perpetual → never; blank expiry → never; non-Active → never.
		self.assertIsNone(document_should_alert({"is_perpetual": 1, "expiry_date": "2026-01-08"}, "2026-01-01"))
		self.assertIsNone(document_should_alert({"is_perpetual": 0, "expiry_date": None}, "2026-01-01"))
		self.assertIsNone(
			document_should_alert({"is_perpetual": 0, "status": "Cancelled", "expiry_date": "2026-01-08"}, "2026-01-01")
		)

	def test_should_alert_active_returns_milestone(self):
		self.assertEqual(
			document_should_alert({"is_perpetual": 0, "status": "Active", "expiry_date": "2026-01-08"}, "2026-01-01"), 7
		)

	def test_deed_never_alerts_regression(self):
		# Constraint #4: a perpetual document (deed / VAT) can NEVER produce a milestone, on any
		# date, even with an (erroneously) populated expiry — the sweep can never remind on a deed.
		for day in ("2026-01-01", "2026-06-30", "2027-01-01"):
			self.assertIsNone(document_should_alert({"is_perpetual": 1, "expiry_date": "2026-01-01"}, day))

	def test_reminder_detail_embeds_expiry_for_renewal_rearm(self):
		# Same document, two different expiries → two different keys, so renewing (new expiry)
		# re-arms the alert instead of being suppressed by last cycle's log row.
		a = document_reminder_detail("LD-2026-00001", "2026-01-08", 7)
		b = document_reminder_detail("LD-2026-00001", "2027-01-08", 7)
		self.assertNotEqual(a, b)
		self.assertEqual(a, "LD-2026-00001|2026-01-08|T-7")

	def test_document_status(self):
		self.assertEqual(document_status("2026-01-08", "2026-01-01", is_perpetual=True), "Perpetual")
		self.assertEqual(document_status(None, "2026-01-01", is_perpetual=False), "Perpetual")
		self.assertEqual(document_status("2025-12-31", "2026-01-01", is_perpetual=False), "Expired")
		self.assertEqual(document_status("2026-01-20", "2026-01-01", is_perpetual=False), "Due Soon")
		self.assertEqual(document_status("2026-06-01", "2026-01-01", is_perpetual=False), "OK")


class TestSeedFuturePeriods(unittest.TestCase):
	def _year(self):
		return build_periods("2026-01-01", "2026-12-31", "Monthly", 120000)

	def test_none_cutoff_keeps_all(self):
		self.assertEqual(len(seed_future_periods(self._year(), None)), 12)

	def test_drops_periods_due_before_cutoff(self):
		kept = seed_future_periods(self._year(), "2026-07-01")
		self.assertEqual(len(kept), 6)  # Jul..Dec
		self.assertEqual(str(kept[0]["period_start"]), "2026-07-01")

	def test_cutoff_after_end_keeps_none(self):
		self.assertEqual(seed_future_periods(self._year(), "2027-01-01"), [])

	def test_cutoff_keeps_period_starting_exactly_on_cutoff(self):
		# The period due exactly on the cutoff is still future (>=), so it is kept.
		kept = seed_future_periods(self._year(), "2026-03-01")
		self.assertEqual(len(kept), 10)  # Mar..Dec


class TestLeaseExpiry(unittest.TestCase):
	def test_expired_when_end_strictly_before_today(self):
		self.assertTrue(lease_is_expired("2026-01-01", "2026-01-02"))

	def test_not_expired_on_the_end_day_itself(self):
		self.assertFalse(lease_is_expired("2026-01-02", "2026-01-02"))

	def test_not_expired_when_end_in_future(self):
		self.assertFalse(lease_is_expired("2026-02-01", "2026-01-02"))

	def test_no_end_date_is_never_expired(self):
		self.assertFalse(lease_is_expired(None, "2026-01-02"))


class TestZatcaVatRegex(unittest.TestCase):
	def test_valid(self):
		self.assertTrue(ZATCA_VAT_RE.match("300000000000003"))

	def test_invalid_short(self):
		self.assertFalse(ZATCA_VAT_RE.match("123"))

	def test_invalid_boundaries(self):
		self.assertFalse(ZATCA_VAT_RE.match("100000000000001"))


class TestEscalatedPeriods(unittest.TestCase):
	def test_zero_pct_identical_to_plain(self):
		a = build_periods("2026-01-01", "2027-12-31", "Monthly", 120000)
		b = build_escalated_periods("2026-01-01", "2027-12-31", "Monthly", 120000, 0)
		self.assertEqual(a, b)

	def test_two_year_monthly_five_pct(self):
		ps = build_escalated_periods("2026-01-01", "2027-12-31", "Monthly", 120000, 5)
		self.assertEqual(len(ps), 24)
		year1 = round(sum(p["base_amount"] for p in ps[:12]), 2)
		year2 = round(sum(p["base_amount"] for p in ps[12:]), 2)
		self.assertEqual(year1, 120000)
		self.assertEqual(year2, 126000)  # +5%
		# numbering continues across years
		self.assertEqual([p["period_no"] for p in ps], list(range(1, 25)))

	def test_partial_second_year_is_exact(self):
		# 18-month lease: year 2 has exactly 6 full months at the escalated rate.
		ps = build_escalated_periods("2026-01-01", "2027-06-30", "Monthly", 120000, 5)
		self.assertEqual(len(ps), 18)
		year2 = round(sum(p["base_amount"] for p in ps[12:]), 2)
		self.assertEqual(year2, 63000)  # 126000 / 2

	def test_three_year_compounding(self):
		ps = build_escalated_periods("2026-01-01", "2028-12-31", "Annual", 100000, 10)
		self.assertEqual([p["base_amount"] for p in ps], [100000, 110000, 121000.0])


class TestUnusedRentCredit(unittest.TestCase):
	def test_termination_before_period_credits_full(self):
		self.assertEqual(unused_rent_credit("2026-02-01", "2026-02-28", 2800, "2026-01-15"), 2800)

	def test_termination_mid_period_prorates_by_days(self):
		# 30-day period, termination on day 15 → 15 unused days.
		self.assertEqual(unused_rent_credit("2026-04-01", "2026-04-30", 3000, "2026-04-15"), 1500)

	def test_termination_on_period_end_credits_nothing(self):
		self.assertEqual(unused_rent_credit("2026-04-01", "2026-04-30", 3000, "2026-04-30"), 0.0)

	def test_termination_after_period_credits_nothing(self):
		self.assertEqual(unused_rent_credit("2026-04-01", "2026-04-30", 3000, "2026-05-10"), 0.0)

	def test_last_day_only_unused(self):
		# termination on day 29 of 30 → exactly one unused day.
		self.assertEqual(unused_rent_credit("2026-04-01", "2026-04-30", 3000, "2026-04-29"), 100.0)


class TestEscalationBoundaries(unittest.TestCase):
	def test_anniversary_end_no_sliver_period(self):
		# End date exactly ON the anniversary: same coverage as pct=0 (no bogus 1-day period).
		flat = build_periods("2026-01-01", "2027-01-01", "Monthly", 120000)
		esc = build_escalated_periods("2026-01-01", "2027-01-01", "Monthly", 120000, 5)
		self.assertEqual(len(esc), len(flat))
		self.assertEqual(_total(esc), _total(flat))

	def test_feb29_start_contiguous_coverage(self):
		# Feb-29 start: every period_start must be exactly prev period_end + 1 day (no gaps).
		import datetime
		ps = build_escalated_periods("2024-02-29", "2029-02-27", "Annual", 100000, 5)
		for a, b in zip(ps, ps[1:]):
			self.assertEqual(
				b["period_start"], a["period_end"] + datetime.timedelta(days=1),
				f"gap between {a['period_end']} and {b['period_start']}",
			)

	def test_escalation_segments_counts(self):
		self.assertEqual(escalation_segments("2026-01-01", "2026-12-31"), 1)
		self.assertEqual(escalation_segments("2026-01-01", "2028-12-31"), 3)
		# End ON the anniversary → the sliver year does NOT count (mirror of the builder).
		self.assertEqual(escalation_segments("2026-01-01", "2027-01-01"), 1)
		self.assertEqual(escalation_segments("2026-01-01", "2027-06-30"), 2)
