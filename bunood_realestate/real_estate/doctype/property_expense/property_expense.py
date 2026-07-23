# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from bunood_realestate.real_estate.gl_utils import require_cost_center


class PropertyExpense(Document):
	def validate(self):
		if flt(self.amount) <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		if not self.expense_date:
			self.expense_date = nowdate()
		# Keep the dimension consistent: a chosen unit must belong to the property.
		if self.unit:
			up = frappe.db.get_value("Real Estate Unit", self.unit, "property")
			if up != self.property:
				frappe.throw(_("The selected unit does not belong to this property."))

	def on_submit(self):
		self._post_journal()
		self.db_set("status", "Posted")

	def on_cancel(self):
		# Reverse the GL by cancelling the Journal Entry (single source of truth).
		if self.journal_entry:
			je = frappe.get_doc("Journal Entry", self.journal_entry)
			if je.docstatus == 1:
				je.cancel()
		self.db_set("status", "Cancelled")

	def _post_journal(self):
		cost_center = require_cost_center(self.company)

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.multi_currency = 0
		je.posting_date = self.expense_date
		remark = _("Property expense — {0} · {1}").format(self.property, self.category)
		if self.beneficiary:
			remark += f" · {self.beneficiary}"
		je.user_remark = remark

		# DR the expense (P&L), tagged with the Property (+ Unit) accounting dimension so
		# per-property P&L is real GL. CR bank/cash. No parallel ledger.
		debit = {
			"account": self.expense_account,
			"debit_in_account_currency": flt(self.amount),
			"property": self.property,
		}
		if self.unit:
			debit["real_estate_unit"] = self.unit
		debit["cost_center"] = cost_center
		je.append("accounts", debit)

		credit = {
			"account": self.paid_from,
			"credit_in_account_currency": flt(self.amount),
			"cost_center": cost_center,
		}
		je.append("accounts", credit)

		je.flags.ignore_permissions = True
		je.insert()
		je.submit()
		self.db_set("journal_entry", je.name)
