# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test for the owner-payout idempotency guard (needs a configured site).
Run:  bench --site <site> run-tests --app bunood_realestate --module \
      bunood_realestate.real_estate.doctype.owner_payout.test_owner_payout"""

import frappe
from frappe.tests.utils import FrappeTestCase

from bunood_realestate.real_estate.management import compute_owner_payout, generate_owner_payout


class TestOwnerPayoutPure(FrappeTestCase):
	def test_compute_split(self):
		self.assertEqual(compute_owner_payout(10000, 10), {"rent_base": 10000.0, "fee": 1000.0, "owner_payout": 9000.0})
		self.assertEqual(compute_owner_payout(10000, 0)["owner_payout"], 10000.0)


class TestOwnerPayoutIdempotency(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured on this site")
		self.company = companies[0]
		if not frappe.db.get_value("Company", self.company, "default_payable_account"):
			self.skipTest("Company has no Default Payable Account")

		self.model = frappe.db.get_value("RE Management Model", {"behavior": "managed"}, "name")
		if not self.model:
			self.model = frappe.get_doc({
				"doctype": "RE Management Model", "title": "Managed-Test", "behavior": "managed"
			}).insert(ignore_permissions=True).name

		expense = frappe.get_all(
			"Account",
			filters={"company": self.company, "root_type": "Expense", "is_group": 0},
			pluck="name", limit=1,
		)
		if not expense:
			self.skipTest("No expense account on the company")
		# Rent income account identifies RENT cash (vs utility/service charge cash) in the
		# cash-basis owner-payout query. The rent invoices in this test use the SAME account.
		self.income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		if not self.income:
			self.skipTest("No income account on the company")
		self.income = self.income[0]
		# The rent Service Item is the positive rent-line discriminator in the cash-basis
		# collected query — the test rent invoices below must use this exact item.
		self.rent_item = "Bunood Test Rent"
		if not frappe.db.exists("Item", self.rent_item):
			frappe.get_doc({
				"doctype": "Item", "item_code": self.rent_item, "item_name": self.rent_item,
				"is_stock_item": 0,
				"item_group": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		settings = frappe.get_single("Real Estate Settings")
		settings.owner_payout_expense_account = expense[0]
		settings.rent_income_account = self.income
		settings.default_rent_item = self.rent_item
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)

		self.owner = frappe.get_doc({
			"doctype": "Supplier", "supplier_name": "Bunood Test Owner", "supplier_group": frappe.get_all("Supplier Group", pluck="name", limit=1)[0]
		}).insert(ignore_permissions=True).name if not frappe.db.exists("Supplier", "Bunood Test Owner") else "Bunood Test Owner"

		self.prop = frappe.get_doc({
			"doctype": "Property",
			"property_name": "Bunood Payout Test Property",
			"company": self.company,
			"business_type": self._business_type(),
			"management_model": self.model,
			"owner_party": self.owner,
			"management_fee_percentage": 10,
		}).insert(ignore_permissions=True).name

	def _business_type(self):
		name = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name")
		if name:
			return name
		return frappe.get_doc({
			"doctype": "RE Business Type", "title": "Exempt-Test", "vat_treatment": "Exempt"
		}).insert(ignore_permissions=True).name

	def test_overlapping_window_is_rejected(self):
		"""An already-Posted payout blocks any overlapping window — no double-pay."""
		frappe.get_doc({
			"doctype": "Owner Payout",
			"property": self.prop,
			"owner_party": self.owner,
			"company": self.company,
			"from_date": "2026-01-01",
			"to_date": "2026-01-31",
			"owner_payout": 9000,
			"status": "Posted",
		}).insert(ignore_permissions=True)

		# Jan 15 – Feb 15 overlaps the Jan payout → must raise before posting anything.
		self.assertRaises(
			frappe.ValidationError,
			generate_owner_payout,
			property=self.prop,
			from_date="2026-01-15",
			to_date="2026-02-15",
		)

	def test_payout_je_is_property_tagged_and_cost_centered(self):
		"""End-to-end: rent income tagged with the property → payout JE debits the owner
		expense with the SAME property dimension and a company-matching cost center, so
		per-property P&L nets to the fee. Regression for the missing dimension / cost center."""
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		cc = frappe.get_cached_value("Company", self.company, "cost_center")
		if not (income and cc):
			self.skipTest("Company missing income account / cost center")
		si = self._make_rent_invoice(income[0], cc, rate=10000, on="2026-03-10")
		self.addCleanup(self._cancel, "Sales Invoice", si)
		pe = self._pay_invoice(si, on="2026-03-15")  # cash-basis: owner paid on collected rent
		self.addCleanup(self._cancel, "Payment Entry", pe)

		res = generate_owner_payout(property=self.prop, from_date="2026-03-01", to_date="2026-03-31")
		self.addCleanup(self._cancel_payout, res["owner_payout_record"])
		self.assertEqual(res["rent_base"], 10000.0)
		self.assertEqual(res["owner_payout"], 9000.0)  # 10% fee

		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		self.assertEqual(round(je.total_debit, 2), 9000.0)
		dr = [a for a in je.accounts if a.debit_in_account_currency > 0][0]
		self.assertEqual(dr.get("property"), self.prop, "payout expense must carry the property dimension")
		self.assertTrue(dr.cost_center, "payout expense (P&L) needs a cost center")
		self.assertEqual(frappe.db.get_value("Cost Center", dr.cost_center, "company"), self.company)

	def test_je_cancel_marks_record_cancelled_and_reallows_repost(self):
		"""Cancelling the payout JE reverses the GL, so the Owner Payout must flip to
		Cancelled (via the JE doc-event) and the same period must become payable again.
		Regression for the double-pay hole: a stale Posted record must not outlive its JE."""
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		cc = frappe.get_cached_value("Company", self.company, "cost_center")
		if not (income and cc):
			self.skipTest("Company missing income account / cost center")
		si = self._make_rent_invoice(income[0], cc, rate=10000, on="2026-05-10")
		self.addCleanup(self._cancel, "Sales Invoice", si)
		pe = self._pay_invoice(si, on="2026-05-15")
		self.addCleanup(self._cancel, "Payment Entry", pe)

		res = generate_owner_payout(property=self.prop, from_date="2026-05-01", to_date="2026-05-31")
		self.addCleanup(self._cancel_payout, res["owner_payout_record"])
		record, je_name = res["owner_payout_record"], res["journal_entry"]
		self.assertEqual(frappe.db.get_value("Owner Payout", record, "status"), "Posted")

		# Cancel the backing JE → the doc-event must mark the record Cancelled.
		frappe.get_doc("Journal Entry", je_name).cancel()
		self.assertEqual(
			frappe.db.get_value("Owner Payout", record, "status"), "Cancelled",
			"cancelling the payout JE must flip the Owner Payout to Cancelled",
		)

		# The window is now payable again (GL was reversed) — the guard must NOT block it.
		res2 = generate_owner_payout(property=self.prop, from_date="2026-05-01", to_date="2026-05-31")
		self.addCleanup(self._cancel_payout, res2["owner_payout_record"])
		self.assertEqual(res2["owner_payout"], 9000.0)

	def test_cannot_delete_posted_payout_while_je_live(self):
		"""on_trash guard: deleting a Posted payout whose JE is still submitted must raise —
		otherwise the JE is orphaned in the GL and the next run pays the owner twice."""
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		cc = frappe.get_cached_value("Company", self.company, "cost_center")
		if not (income and cc):
			self.skipTest("Company missing income account / cost center")
		si = self._make_rent_invoice(income[0], cc, rate=10000, on="2026-06-10")
		self.addCleanup(self._cancel, "Sales Invoice", si)
		pe = self._pay_invoice(si, on="2026-06-15")
		self.addCleanup(self._cancel, "Payment Entry", pe)
		res = generate_owner_payout(property=self.prop, from_date="2026-06-01", to_date="2026-06-30")
		self.addCleanup(self._cancel_payout, res["owner_payout_record"])

		self.assertRaises(
			frappe.ValidationError,
			frappe.delete_doc, "Owner Payout", res["owner_payout_record"], ignore_permissions=True,
		)

	def _make_rent_invoice(self, income_account, cost_center, rate, on):
		customer = "Bunood Test Tenant"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc({
				"doctype": "Customer", "customer_name": customer,
				"customer_group": frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0],
				"territory": frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", limit=1)[0],
			}).insert(ignore_permissions=True)
		item = self.rent_item  # must match settings.default_rent_item (rent-line discriminator)
		si = frappe.get_doc({
			"doctype": "Sales Invoice", "customer": customer, "company": self.company,
			"posting_date": on, "set_posting_time": 1, "due_date": on,
			"property": self.prop,  # parent-level dimension, mirrors tasks.py rent invoices
			"items": [{
				"item_code": item, "qty": 1, "rate": rate, "income_account": income_account,
				"cost_center": cost_center, "property": self.prop,
			}],
		})
		si.flags.ignore_permissions = True
		si.insert(ignore_permissions=True)
		si.submit()
		return si.name

	def test_je_settlement_counts_as_collected(self):
		"""Rent collected via a Journal Entry (Dr Bank / Cr Debtors) — not a Payment Entry — is
		still counted by the cash-basis payout (PLE-based). Regression for the JE-blindness bug."""
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		cc = frappe.get_cached_value("Company", self.company, "cost_center")
		if not (income and cc):
			self.skipTest("Company missing income account / cost center")
		si = self._make_rent_invoice(income[0], cc, rate=10000, on="2026-07-10")
		self.addCleanup(self._cancel, "Sales Invoice", si)
		je = self._settle_via_je(si, amount=10000, on="2026-07-15")
		self.addCleanup(self._cancel, "Journal Entry", je)

		res = generate_owner_payout(property=self.prop, from_date="2026-07-01", to_date="2026-07-31")
		self.addCleanup(self._cancel_payout, res["owner_payout_record"])
		self.assertEqual(res["rent_base"], 10000.0)
		self.assertEqual(res["owner_payout"], 9000.0)

	def test_amending_payout_je_keeps_single_posted_payout(self):
		"""Amending the payout JE (cancel + resubmit) must re-attach the Owner Payout and keep
		it Posted, and must NOT let a re-run pay the owner twice. Regression for amend double-pay."""
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		cc = frappe.get_cached_value("Company", self.company, "cost_center")
		if not (income and cc):
			self.skipTest("Company missing income account / cost center")
		si = self._make_rent_invoice(income[0], cc, rate=10000, on="2026-08-10")
		self.addCleanup(self._cancel, "Sales Invoice", si)
		pe = self._pay_invoice(si, on="2026-08-15")
		self.addCleanup(self._cancel, "Payment Entry", pe)
		res = generate_owner_payout(property=self.prop, from_date="2026-08-01", to_date="2026-08-31")
		self.addCleanup(self._cancel_payout, res["owner_payout_record"])
		record, je_name = res["owner_payout_record"], res["journal_entry"]

		je = frappe.get_doc("Journal Entry", je_name)
		je.cancel()
		amended = frappe.copy_doc(je)
		amended.amended_from = je_name
		amended.flags.ignore_permissions = True
		amended.insert(ignore_permissions=True)
		amended.submit()

		self.assertEqual(
			frappe.db.get_value("Owner Payout", record, "journal_entry"), amended.name,
			"the payout must be re-attached to the amended JE",
		)
		self.assertEqual(frappe.db.get_value("Owner Payout", record, "status"), "Posted")
		# The window is still covered by a Posted payout → a re-run must be rejected (no double-pay).
		self.assertRaises(
			frappe.ValidationError, generate_owner_payout,
			property=self.prop, from_date="2026-08-01", to_date="2026-08-31",
		)

	def test_cancelling_settling_payment_flags_owner_payout(self):
		"""Cancelling a settling Payment Entry after a payout is Posted leaves a visible warning
		comment on the affected Owner Payout (cash-basis over-pay visibility, no silent gap)."""
		if "property" not in frappe.get_meta("Sales Invoice Item").fields_map:
			self.skipTest("Property accounting dimension not migrated on this site")
		income = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1
		)
		cc = frappe.get_cached_value("Company", self.company, "cost_center")
		if not (income and cc):
			self.skipTest("Company missing income account / cost center")
		si = self._make_rent_invoice(income[0], cc, rate=10000, on="2026-09-10")
		self.addCleanup(self._cancel, "Sales Invoice", si)
		pe = self._pay_invoice(si, on="2026-09-15")
		res = generate_owner_payout(property=self.prop, from_date="2026-09-01", to_date="2026-09-30")
		self.addCleanup(self._cancel_payout, res["owner_payout_record"])

		frappe.get_doc("Payment Entry", pe).cancel()  # e.g. bounced cheque

		comments = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Owner Payout", "reference_name": res["owner_payout_record"],
				"comment_type": "Comment",
			},
			pluck="content",
		)
		self.assertTrue(
			any("cancel" in (c or "").lower() for c in comments),
			"a cancellation warning comment must be posted on the affected owner payout",
		)

	def _settle_via_je(self, si_name, amount, on):
		"""Collect a rent invoice via a Journal Entry (Dr Bank / Cr Debtors), returning its name."""
		si = frappe.get_doc("Sales Invoice", si_name)
		bank = frappe.get_all(
			"Account",
			filters={"company": self.company, "account_type": ["in", ["Bank", "Cash"]], "is_group": 0},
			pluck="name", limit=1,
		)
		if not bank:
			self.skipTest("No bank/cash account on the company")
		je = frappe.get_doc({
			"doctype": "Journal Entry", "company": self.company, "posting_date": on,
			"voucher_type": "Journal Entry",
			"accounts": [
				{"account": si.debit_to, "party_type": "Customer", "party": si.customer,
				 "credit_in_account_currency": amount,
				 "reference_type": "Sales Invoice", "reference_name": si_name},
				{"account": bank[0], "debit_in_account_currency": amount},
			],
		})
		je.flags.ignore_permissions = True
		je.insert(ignore_permissions=True)
		je.submit()
		return je.name

	def _pay_invoice(self, si_name, on):
		"""Fully pay a rent Sales Invoice via a Payment Entry so cash-basis owner payout has
		collected rent to distribute. Returns the Payment Entry name."""
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Sales Invoice", si_name)
		pe.posting_date = on
		pe.reference_no = "TEST-" + si_name
		pe.reference_date = on
		if not pe.paid_to:
			bank = frappe.get_all(
				"Account",
				filters={"company": self.company, "account_type": ["in", ["Bank", "Cash"]], "is_group": 0},
				pluck="name", limit=1,
			)
			if not bank:
				self.skipTest("No bank/cash account on the company")
			pe.paid_to = bank[0]
		pe.flags.ignore_permissions = True
		pe.insert(ignore_permissions=True)
		pe.submit()
		return pe.name

	def _cancel(self, dt, name):
		try:
			d = frappe.get_doc(dt, name)
			if d.docstatus == 1:
				d.cancel()
		except Exception:
			pass

	def _cancel_payout(self, name):
		try:
			doc = frappe.get_doc("Owner Payout", name)
			if doc.journal_entry:
				je = frappe.get_doc("Journal Entry", doc.journal_entry)
				if je.docstatus == 1:
					je.cancel()
			frappe.delete_doc("Owner Payout", name, force=True, ignore_permissions=True)
		except Exception:
			pass
