# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate


class LandExpense(Document):
	def validate(self):
		if flt(self.amount) <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		if not self.expense_date:
			self.expense_date = nowdate()
		if self.land_contract:
			cl = frappe.db.get_value("Land Contract", self.land_contract, "land")
			if cl and cl != self.land:
				frappe.throw(_("The land contract belongs to a different land."))

	def on_submit(self):
		settings = frappe.get_single("Real Estate Settings")
		cost_center = settings.default_cost_center or frappe.get_cached_value("Company", self.company, "cost_center")

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.multi_currency = 0
		je.posting_date = self.expense_date
		remark = _("Land expense — {0} · {1}").format(self.land, self.category)
		if self.beneficiary:
			remark += f" · {self.beneficiary}"
		je.user_remark = remark

		# DR expense (P&L) tagged with the Land dimension → per-land P&L. CR bank/cash.
		debit = {"account": self.expense_account, "debit_in_account_currency": flt(self.amount), "land": self.land}
		if cost_center:
			debit["cost_center"] = cost_center
		je.append("accounts", debit)

		credit = {"account": self.paid_from, "credit_in_account_currency": flt(self.amount)}
		if cost_center:
			credit["cost_center"] = cost_center
		je.append("accounts", credit)

		je.flags.ignore_permissions = True
		je.insert()
		je.submit()
		self.db_set("journal_entry", je.name)
		self.db_set("status", "Posted")

	def on_cancel(self):
		if self.journal_entry:
			je = frappe.get_doc("Journal Entry", self.journal_entry)
			if je.docstatus == 1:
				je.cancel()
		self.db_set("status", "Cancelled")
