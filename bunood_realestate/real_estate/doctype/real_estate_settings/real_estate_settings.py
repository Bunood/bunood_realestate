# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class RealEstateSettings(Document):
	"""Single doctype holding the accounting defaults for the DEFAULT company (multi-company
	sites add a Real Estate Company Profile per extra company — a profile for the default
	company overrides this page). Config-over-code: no account is hardcoded.

	Validation here is WARN-only by design: a live site with a latent mismatch must still
	be able to save (the app's Warn→Block philosophy). The strict day-one blocks live on
	Real Estate Company Profile, which is new and can afford them."""

	def validate(self):
		self._warn_wrong_company_links()
		self._note_profile_override()

	def _warn_wrong_company_links(self):
		if not self.company:
			return
		for f in (
			"rent_income_account",
			"receivable_account",
			"tenant_deposit_account",
			"maintenance_expense_account",
			"owner_payout_expense_account",
			"opening_balance_account",
			"deduction_income_account",
		):
			acc = self.get(f)
			if not acc:
				continue
			acc_company = frappe.db.get_value("Account", acc, "company")
			if acc_company and acc_company != self.company:
				frappe.msgprint(
					_("{0}: account {1} belongs to company {2}, not {3} — postings for {3} will fail.").format(
						_(self.meta.get_label(f)), acc, acc_company, self.company
					),
					indicator="orange",
				)

	def _note_profile_override(self):
		if not self.company:
			return
		if frappe.db.table_exists("Real Estate Company Profile") and frappe.db.exists(
			"Real Estate Company Profile", {"company": self.company, "enabled": 1}
		):
			frappe.msgprint(
				_(
					"An enabled Real Estate Company Profile exists for {0} — the account fields on "
					"this page are INACTIVE for it; edit the Company Profile instead."
				).format(self.company),
				indicator="orange",
			)
