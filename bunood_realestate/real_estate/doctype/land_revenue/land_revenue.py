# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from bunood_realestate.real_estate.gl_utils import require_cost_center


class LandRevenue(Document):
	def validate(self):
		if flt(self.amount) <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		if not self.revenue_date:
			self.revenue_date = nowdate()
		if self.land_contract:
			cl = frappe.db.get_value("Land Contract", self.land_contract, "land")
			if cl and cl != self.land:
				frappe.throw(_("The land contract belongs to a different land."))

	def on_submit(self):
		cost_center = require_cost_center(self.company)

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.multi_currency = 0
		je.posting_date = self.revenue_date
		je.user_remark = _("Land revenue — {0} · {1}").format(self.land, self.category)

		# DR bank/cash (received). CR income (P&L) tagged with the Land dimension → per-land P&L.
		debit = {"account": self.received_in, "debit_in_account_currency": flt(self.amount), "cost_center": cost_center}
		je.append("accounts", debit)

		credit = {
			"account": self.income_account,
			"credit_in_account_currency": flt(self.amount),
			"land": self.land,
			"cost_center": cost_center,
		}
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
