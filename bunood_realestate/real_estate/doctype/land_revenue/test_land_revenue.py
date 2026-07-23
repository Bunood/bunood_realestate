# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration tests for Land Revenue + Land Expense (need a site with a Company +
accounts + the Land accounting dimension migrated)."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt


class TestLandFinance(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		inc = frappe.get_all("Account", filters={"company": self.company, "root_type": "Income", "is_group": 0}, pluck="name", limit=1)
		exp = frappe.get_all("Account", filters={"company": self.company, "root_type": "Expense", "is_group": 0}, pluck="name", limit=1)
		cash = frappe.get_all("Account", filters={"company": self.company, "account_type": ["in", ["Bank", "Cash"]], "is_group": 0}, pluck="name", limit=1)
		if not (inc and exp and cash):
			self.skipTest("Company missing income/expense/cash accounts")
		self.inc, self.exp, self.cash = inc[0], exp[0], cash[0]
		if "land" not in frappe.get_meta("Journal Entry Account").fields_map:
			self.skipTest("Land accounting dimension not migrated on this site")
		self.land = frappe.db.get_value("Land", {"land_name": "Test Land"})
		if not self.land:
			self.land = frappe.get_doc({"doctype": "Land", "land_name": "Test Land", "company": self.company, "land_type": "Raw"}).insert(ignore_permissions=True).name

	def test_revenue_posts_land_tagged_je(self):
		rev = frappe.get_doc({
			"doctype": "Land Revenue", "land": self.land, "category": "Rent",
			"revenue_date": "2026-07-01", "amount": 1000, "income_account": self.inc, "received_in": self.cash,
		})
		rev.insert(ignore_permissions=True)
		rev.submit()
		self.addCleanup(self._cleanup, "Land Revenue", rev.name)
		je = frappe.get_doc("Journal Entry", rev.journal_entry)
		self.assertEqual(je.docstatus, 1)
		self.assertEqual(round(flt(je.total_credit), 2), 1000)
		income_line = [a for a in je.accounts if flt(a.credit_in_account_currency) > 0][0]
		self.assertEqual(income_line.get("land"), self.land)
		rev.reload(); rev.cancel()
		self.assertEqual(frappe.db.get_value("Journal Entry", rev.journal_entry, "docstatus"), 2)

	def test_expense_posts_land_tagged_je(self):
		ex = frappe.get_doc({
			"doctype": "Land Expense", "land": self.land, "category": "Guarding",
			"expense_date": "2026-07-01", "amount": 300, "expense_account": self.exp, "paid_from": self.cash,
		})
		ex.insert(ignore_permissions=True)
		ex.submit()
		self.addCleanup(self._cleanup, "Land Expense", ex.name)
		je = frappe.get_doc("Journal Entry", ex.journal_entry)
		self.assertEqual(round(flt(je.total_debit), 2), 300)
		exp_line = [a for a in je.accounts if flt(a.debit_in_account_currency) > 0][0]
		self.assertEqual(exp_line.get("land"), self.land)

	def _cleanup(self, dt, name):
		try:
			d = frappe.get_doc(dt, name)
			if d.docstatus == 1:
				d.cancel()
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
		except Exception:
			pass
