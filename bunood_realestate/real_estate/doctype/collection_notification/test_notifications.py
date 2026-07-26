# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration tests for the renewal/collection notification engine (need a site).
The pure milestone math is covered in test_rent_schedule.TestExpiryMilestone."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from bunood_realestate.real_estate import notifications


class TestNotificationEngine(FrappeTestCase):
	def setUp(self):
		# Any existing Lease Contract lets the Collection Notification link resolve; the
		# emit/idempotency logic doesn't depend on the lease being Active/submitted.
		lease = frappe.get_all(
			"Lease Contract", fields=["name", "customer", "company", "property"], limit=1
		)
		if not lease:
			self.skipTest("No Lease Contract on this site to attach a notification to")
		self.lease = frappe._dict(lease[0])
		self.lease.end_date = add_days(nowdate(), 30)
		self.lease.auto_renew = 0

	def _clear(self, detail, ntype):
		for n in frappe.get_all(
			"Collection Notification",
			filters={"lease_contract": self.lease.name, "notification_type": ntype, "detail": detail},
			pluck="name",
		):
			frappe.delete_doc("Collection Notification", n, force=True, ignore_permissions=True)

	def test_expiry_alert_is_logged_once(self):
		self._clear("T-30", "Renewal")
		self.addCleanup(self._clear, "T-30", "Renewal")

		first = notifications._emit_expiry_alert(self.lease, 30)
		self.assertTrue(first, "first emit should create the alert")
		logs = frappe.get_all(
			"Collection Notification",
			filters={"lease_contract": self.lease.name, "notification_type": "Renewal", "detail": "T-30"},
		)
		self.assertEqual(len(logs), 1)

		# Re-run: the existing log is the idempotency key → no second alert.
		second = notifications._emit_expiry_alert(self.lease, 30)
		self.assertFalse(second, "second emit must be suppressed")
		logs = frappe.get_all(
			"Collection Notification",
			filters={"lease_contract": self.lease.name, "notification_type": "Renewal", "detail": "T-30"},
		)
		self.assertEqual(len(logs), 1, "still exactly one log after re-run")

	def test_upcoming_renewals_returns_list(self):
		# Smoke: company-scoped, no crash, returns a list.
		self.assertIsInstance(notifications.upcoming_renewals(90), list)
