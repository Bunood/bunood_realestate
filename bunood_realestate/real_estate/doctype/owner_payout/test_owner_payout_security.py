# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Security regressions from the master-prompt conformance audit:
- gl_utils.assert_company_access gates cross-company money posting.
- core.notify.notify is NOT whitelisted (no email/SMS open relay)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.core import notify as notify_mod
from bunood_realestate.real_estate.gl_utils import assert_company_access


class TestMoneyGuards(FrappeTestCase):
	def test_assert_company_access_rejects_unknown_company(self):
		self.assertRaises(frappe.PermissionError, assert_company_access, "No Such Company ZZZ")

	def test_assert_company_access_rejects_empty(self):
		self.assertRaises(frappe.PermissionError, assert_company_access, None)

	def test_assert_company_access_allows_permitted_company(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		# As Administrator every company is permitted → must not raise.
		assert_company_access(companies[0])

	def test_notify_is_not_whitelisted(self):
		# The email/SMS fan-out must be server-internal only (no /api/method exposure),
		# otherwise any authenticated user (incl. a portal tenant) could send arbitrary mail/SMS.
		self.assertNotIn(notify_mod.notify, getattr(frappe, "whitelisted", set()))
		self.assertFalse(getattr(notify_mod.notify, "__func__", notify_mod.notify).__dict__.get("whitelisted"))
