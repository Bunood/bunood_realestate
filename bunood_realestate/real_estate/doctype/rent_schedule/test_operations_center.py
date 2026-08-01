# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure tests for the invoicing policy + Operations Center health computation
(docs/plan-invoicing-automation.md). No site needed for the pure classes.

Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.rent_schedule.test_operations_center"""

import unittest

from bunood_realestate.real_estate.invoicing_policy import (
	DAYS_BEFORE_DUE,
	MANUAL,
	ON_DUE_DATE,
	ON_PAYMENT,
	auto_issues,
	resolve,
)
from bunood_realestate.real_estate.operations import (
	CANCELLED,
	DRAFT_INVOICE,
	DUE_SOON,
	ISSUED,
	OVERDUE_UNISSUED,
	OVERDUE_UNPAID,
	PAID,
	PARTIALLY_PAID,
	PENDING_RECEIPT,
	PLANNED,
	compute_health,
)

TODAY = "2026-08-01"


class TestInvoicingPolicy(unittest.TestCase):
	def test_manual_and_on_payment_never_auto_issue(self):
		self.assertFalse(auto_issues(MANUAL))
		self.assertFalse(auto_issues(ON_PAYMENT))

	def test_auto_policies_issue(self):
		self.assertTrue(auto_issues(ON_DUE_DATE))
		self.assertTrue(auto_issues(DAYS_BEFORE_DUE))

	def test_lead_days_only_apply_to_days_before_due(self):
		self.assertEqual(resolve(DAYS_BEFORE_DUE, 7), (DAYS_BEFORE_DUE, 7))
		self.assertEqual(resolve(ON_DUE_DATE, 7), (ON_DUE_DATE, 0))
		self.assertEqual(resolve(MANUAL, 7), (MANUAL, 0))
		self.assertEqual(resolve(ON_PAYMENT, 7), (ON_PAYMENT, 0))

	def test_negative_lead_days_floored(self):
		self.assertEqual(resolve(DAYS_BEFORE_DUE, -3), (DAYS_BEFORE_DUE, 0))

	def test_blank_or_unknown_policy_fails_safe_to_manual(self):
		# Issuance submits, and a submitted invoice is ZATCA-reported and uncancellable.
		# So the safe direction on a half-migrated site is to issue NOTHING.
		self.assertEqual(resolve(None, 0), (MANUAL, 0))
		self.assertEqual(resolve("", 0), (MANUAL, 0))
		self.assertEqual(resolve("Nonsense", 0), (MANUAL, 0))
		self.assertFalse(auto_issues(resolve(None, 0)[0]))


def row(**kw):
	base = {
		"status": "Planned", "due_date": None, "sales_invoice": None,
		"invoice_docstatus": None, "grand_total": 0, "outstanding": 0, "pending": 0,
	}
	base.update(kw)
	return base


class TestHealth(unittest.TestCase):
	def test_future_installment_is_planned(self):
		h = compute_health(row(due_date="2026-09-15"), TODAY, 5)
		self.assertEqual(h["key"], PLANNED)

	def test_within_window_is_due_soon(self):
		h = compute_health(row(due_date="2026-08-04"), TODAY, 5)
		self.assertEqual(h["key"], DUE_SOON)
		self.assertEqual(h["days"], -3)  # 3 days until due

	def test_window_boundary_is_inclusive(self):
		self.assertEqual(compute_health(row(due_date="2026-08-06"), TODAY, 5)["key"], DUE_SOON)
		self.assertEqual(compute_health(row(due_date="2026-08-07"), TODAY, 5)["key"], PLANNED)

	def test_due_today_is_due_soon(self):
		self.assertEqual(compute_health(row(due_date=TODAY), TODAY, 5)["key"], DUE_SOON)

	def test_past_due_without_invoice_is_overdue_unissued(self):
		h = compute_health(row(due_date="2026-07-09"), TODAY, 5)
		self.assertEqual(h["key"], OVERDUE_UNISSUED)
		self.assertEqual(h["days"], 23)

	def test_submitted_unpaid_invoice_before_due_is_issued(self):
		h = compute_health(
			row(status="Invoiced", due_date="2026-08-20", sales_invoice="SI-1",
			    invoice_docstatus=1, grand_total=1000, outstanding=1000),
			TODAY, 5,
		)
		self.assertEqual(h["key"], ISSUED)

	def test_submitted_unpaid_invoice_past_due_is_overdue(self):
		h = compute_health(
			row(status="Invoiced", due_date="2026-07-09", sales_invoice="SI-1",
			    invoice_docstatus=1, grand_total=1000, outstanding=1000),
			TODAY, 5,
		)
		self.assertEqual(h["key"], OVERDUE_UNPAID)
		self.assertEqual(h["days"], 23)

	def test_partial_payment(self):
		h = compute_health(
			row(status="Invoiced", due_date="2026-07-20", sales_invoice="SI-1",
			    invoice_docstatus=1, grand_total=1000, outstanding=400),
			TODAY, 5,
		)
		self.assertEqual(h["key"], PARTIALLY_PAID)

	def test_settled_invoice_is_paid(self):
		h = compute_health(
			row(status="Invoiced", due_date="2026-07-20", sales_invoice="SI-1",
			    invoice_docstatus=1, grand_total=1000, outstanding=0),
			TODAY, 5,
		)
		self.assertEqual(h["key"], PAID)

	def test_rounding_dust_counts_as_paid(self):
		h = compute_health(
			row(status="Invoiced", due_date="2026-07-20", sales_invoice="SI-1",
			    invoice_docstatus=1, grand_total=1000, outstanding=0.004),
			TODAY, 5,
		)
		self.assertEqual(h["key"], PAID)

	def test_cancelled_invoice_link_is_not_treated_as_issued(self):
		# A cancelled SI (docstatus 2) must fall back to the un-issued journey, never
		# show as "invoiced" — otherwise the row offers "Receive Payment" on a void doc.
		h = compute_health(
			row(status="Planned", due_date="2026-07-09", sales_invoice="SI-1",
			    invoice_docstatus=2, grand_total=1000, outstanding=1000),
			TODAY, 5,
		)
		self.assertEqual(h["key"], OVERDUE_UNISSUED)

	def test_draft_invoice_has_its_own_state(self):
		# Legacy rows from the retired draft-invoice policy: not a tax document, not a
		# receivable — and NOT offered a "Receive Payment" action.
		h = compute_health(
			row(status="Invoiced", due_date="2026-08-20", sales_invoice="SI-1",
			    invoice_docstatus=0, grand_total=1000, outstanding=1000),
			TODAY, 5,
		)
		self.assertEqual(h["key"], DRAFT_INVOICE)

	def test_draft_receipt_covering_balance_is_pending_not_overdue(self):
		# THE regression that mattered: a tenant who paid, whose receipt is awaiting
		# approval, must never render red — late fees and dunning key off this.
		h = compute_health(
			row(status="Invoiced", due_date="2026-07-09", sales_invoice="SI-1",
			    invoice_docstatus=1, grand_total=1150, outstanding=1150, pending=1150),
			TODAY, 5,
		)
		self.assertEqual(h["key"], PENDING_RECEIPT)

	def test_partial_draft_receipt_does_not_mask_the_rest(self):
		h = compute_health(
			row(status="Invoiced", due_date="2026-07-09", sales_invoice="SI-1",
			    invoice_docstatus=1, grand_total=1150, outstanding=1150, pending=500),
			TODAY, 5,
		)
		self.assertEqual(h["key"], OVERDUE_UNPAID)

	def test_cancelled_row_short_circuits(self):
		h = compute_health(row(status="Cancelled", due_date="2026-07-09"), TODAY, 5)
		self.assertEqual(h["key"], CANCELLED)

	def test_zero_window_means_only_today_is_due_soon(self):
		self.assertEqual(compute_health(row(due_date=TODAY), TODAY, 0)["key"], DUE_SOON)
		self.assertEqual(compute_health(row(due_date="2026-08-02"), TODAY, 0)["key"], PLANNED)


if __name__ == "__main__":
	unittest.main()
