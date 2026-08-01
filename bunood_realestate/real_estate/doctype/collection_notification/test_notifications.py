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

	def test_auto_renewals_are_idempotent(self):
		from frappe.utils import add_days, nowdate

		lease = frappe.get_all(
			"Lease Contract",
			filters={
				"status": "Active", "docstatus": 1, "auto_renew": 1,
				"end_date": ["between", [nowdate(), add_days(nowdate(), 30)]],
			},
			pluck="name", limit=1,
		)
		if not lease:
			self.skipTest("No in-window auto-renew lease on this site")
		name = lease[0]
		had_renewal = bool(frappe.db.exists("Lease Contract", {"parent_lease": name}))

		notifications._run_auto_renewals()
		self.assertTrue(frappe.db.exists("Lease Contract", {"parent_lease": name}), "a renewal draft should exist")
		# Every in-window lease now has a renewal → an immediate re-run drafts nothing new.
		self.assertEqual(notifications._run_auto_renewals(), 0)

		if not had_renewal:
			for r in frappe.get_all("Lease Contract", filters={"parent_lease": name, "docstatus": 0}, pluck="name"):
				frappe.delete_doc("Lease Contract", r, force=True, ignore_permissions=True)


class TestDocumentExpiryEngine(FrappeTestCase):
	"""Integration for the document-expiry sweep (needs a site). Pure math is covered in
	test_rent_schedule.TestDocumentExpiry."""

	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		if not frappe.db.exists("DocType", "Legal Document"):
			self.skipTest("Legal Document doctype not migrated")
		self.company = companies[0]
		self.dtype = self._doc_type("Bunood Test CR", perpetual=0)
		self.ptype = self._doc_type("Bunood Test Deed", perpetual=1)

	def _doc_type(self, name, perpetual):
		if not frappe.db.exists("RE Document Type", name):
			frappe.get_doc({
				"doctype": "RE Document Type", "document_type_name": name,
				"is_perpetual": perpetual, "is_active": 1,
			}).insert(ignore_permissions=True)
		return name

	def _legal_doc(self, dtype, expiry, number="CR-1"):
		doc = frappe.get_doc({
			"doctype": "Legal Document", "document_type": dtype,
			"link_doctype": "Company", "link_name": self.company, "company": self.company,
			"document_number": number, "expiry_date": expiry, "status": "Active",
		})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		self.addCleanup(self._purge, doc.name)
		return doc

	def _purge(self, ld_name):
		for n in frappe.get_all("Document Reminder Log", filters={"legal_document": ld_name}, pluck="name"):
			frappe.delete_doc("Document Reminder Log", n, force=True, ignore_permissions=True)
		if frappe.db.exists("Legal Document", ld_name):
			frappe.delete_doc("Legal Document", ld_name, force=True, ignore_permissions=True)

	def _row(self, doc):
		doc.reload()
		return frappe._dict({
			"name": doc.name, "document_type": doc.document_type, "link_doctype": doc.link_doctype,
			"link_name": doc.link_name, "company": doc.company, "expiry_date": doc.expiry_date,
			"document_number": doc.document_number, "is_perpetual": doc.is_perpetual, "status": doc.status,
		})

	def test_emit_is_logged_once_and_rearms_on_renewal(self):
		doc = self._legal_doc(self.dtype, add_days(nowdate(), 7))
		today = nowdate()

		self.assertTrue(notifications._emit_document_alert(self._row(doc), today), "first emit creates the alert")
		self.assertEqual(len(frappe.get_all("Document Reminder Log", {"legal_document": doc.name})), 1)
		# Idempotent re-run.
		self.assertFalse(notifications._emit_document_alert(self._row(doc), today), "second emit suppressed")
		self.assertEqual(len(frappe.get_all("Document Reminder Log", {"legal_document": doc.name})), 1)

		# Renewal-in-place: a new expiry changes the idempotency key → a fresh alert fires.
		doc.expiry_date = add_days(nowdate(), 7 + 365)
		doc.save(ignore_permissions=True)
		# Now inside the 90 (not 7) bucket for the new expiry → alerts again.
		self.assertTrue(notifications._emit_document_alert(self._row(doc), today), "renewal re-arms the alert")
		self.assertEqual(len(frappe.get_all("Document Reminder Log", {"legal_document": doc.name})), 2)

	def test_perpetual_document_never_alerts(self):
		# The deed guard: even though we pass an expiry, a perpetual type nulls it and never alerts.
		doc = self._legal_doc(self.ptype, None, number="DEED-1")
		self.assertFalse(doc.expiry_date, "perpetual doc must carry no expiry")
		self.assertFalse(notifications._emit_document_alert(self._row(doc), nowdate()))
		self.assertEqual(len(frappe.get_all("Document Reminder Log", {"legal_document": doc.name})), 0)

	def test_unique_detail_index_rejects_duplicate(self):
		doc = self._legal_doc(self.dtype, add_days(nowdate(), 30), number="CR-UNIQ")
		detail = notifications.document_reminder_detail(doc.name, doc.expiry_date, 30)
		notifications._log_document_reminder(self._row(doc), 30, detail, "first")
		with self.assertRaises(Exception):
			notifications._log_document_reminder(self._row(doc), 30, detail, "duplicate")

	def test_sweep_and_preview_apis(self):
		self._legal_doc(self.dtype, add_days(nowdate(), 7), number="CR-SWEEP")
		self.assertIsInstance(notifications._run_document_expiry_alerts(), int)
		self.assertIsInstance(notifications.upcoming_document_expiries(90), list)
		self.assertIsInstance(notifications.expiring_documents_count(30), int)
