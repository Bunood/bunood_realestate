# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Integration test (needs a configured site with a Company + accounts).
Run: bench --site <site> run-tests --app bunood_realestate --module \
     bunood_realestate.real_estate.doctype.property_expense.test_property_expense"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt


class TestPropertyExpense(FrappeTestCase):
	def setUp(self):
		companies = frappe.get_all("Company", pluck="name", limit=1)
		if not companies:
			self.skipTest("No Company configured")
		self.company = companies[0]
		exp = frappe.get_all(
			"Account", filters={"company": self.company, "root_type": "Expense", "is_group": 0}, pluck="name", limit=1
		)
		cash = frappe.get_all(
			"Account", filters={"company": self.company, "account_type": ["in", ["Bank", "Cash"]], "is_group": 0}, pluck="name", limit=1
		)
		if not (exp and cash):
			self.skipTest("Company has no expense / bank-cash account")
		self.expense_acc, self.cash_acc = exp[0], cash[0]

		bt = frappe.db.get_value("RE Business Type", {"vat_treatment": "Exempt"}, "name")
		if not bt:
			bt = frappe.get_doc({"doctype": "RE Business Type", "title": "BT-Exempt-Test", "vat_treatment": "Exempt"}).insert(ignore_permissions=True).name
		self.prop = frappe.db.get_value("Property", {"property_name": "PExp Test Property"})
		if not self.prop:
			self.prop = frappe.get_doc({
				"doctype": "Property", "property_name": "PExp Test Property",
				"company": self.company, "business_type": bt,
			}).insert(ignore_permissions=True).name

	def test_submit_posts_dimension_tagged_je_and_cancel_reverses(self):
		exp = frappe.get_doc({
			"doctype": "Property Expense", "property": self.prop, "category": "Utilities",
			"expense_date": "2026-07-01", "amount": 500,
			"expense_account": self.expense_acc, "paid_from": self.cash_acc,
		})
		exp.insert(ignore_permissions=True)
		exp.submit()
		self.addCleanup(self._cleanup, exp.name)

		self.assertTrue(exp.journal_entry, "a Journal Entry should be posted")
		je = frappe.get_doc("Journal Entry", exp.journal_entry)
		self.assertEqual(je.docstatus, 1)
		self.assertEqual(round(flt(je.total_debit), 2), 500)
		# The debit (expense) line carries the Property accounting dimension.
		dr = [a for a in je.accounts if flt(a.debit_in_account_currency) > 0][0]
		self.assertEqual(dr.get("property"), self.prop)
		# Every line carries a cost center that belongs to THIS company (P&L lines
		# mandate one, and a cross-company cost center would be rejected).
		for a in je.accounts:
			self.assertTrue(a.cost_center, "each JE line needs a cost center")
			self.assertEqual(
				frappe.db.get_value("Cost Center", a.cost_center, "company"), self.company
			)
		self.assertEqual(frappe.db.get_value("Property Expense", exp.name, "status"), "Posted")

		# Cancel reverses the GL (JE cancelled) — no orphaned/parallel figure.
		exp.reload()
		exp.cancel()
		self.assertEqual(frappe.db.get_value("Journal Entry", exp.journal_entry, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Property Expense", exp.name, "status"), "Cancelled")

	def test_require_cost_center_throws_for_unknown_company(self):
		# A company with no resolvable cost center must fail early with a clear error,
		# not a raw ERPNext validation mid-submit.
		from bunood_realestate.real_estate.gl_utils import require_cost_center

		self.assertRaises(frappe.ValidationError, require_cost_center, "No Such Company ZZZ")

	def test_amount_must_be_positive(self):
		exp = frappe.get_doc({
			"doctype": "Property Expense", "property": self.prop, "expense_date": "2026-07-01",
			"amount": 0, "expense_account": self.expense_acc, "paid_from": self.cash_acc,
		})
		self.assertRaises(frappe.ValidationError, exp.insert, ignore_permissions=True)

	def _cleanup(self, name):
		try:
			d = frappe.get_doc("Property Expense", name)
			if d.docstatus == 1:
				d.cancel()
			frappe.delete_doc("Property Expense", name, force=True, ignore_permissions=True)
		except Exception:
			pass
