# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Per-company financial configuration. validate() HARD-blocks the wrong-company-account
bug class this layer exists to kill (new feature → strict from day one), and WARNS on
account-type oddities and discriminator drift (operator decisions, never silent)."""

import frappe
from frappe import _
from frappe.model.document import Document

_ACCOUNT_FIELDS = (
	"rent_income_account",
	"receivable_account",
	"tenant_deposit_account",
	"maintenance_expense_account",
	"owner_payout_expense_account",
	"opening_balance_account",
	"deduction_income_account",
)

# WARN-level sanity (root_type / account_type expectations). opening_balance is exempt —
# commonly "Temporary Opening" with its own root.
_ACCOUNT_EXPECT = {
	"rent_income_account": ("root_type", "Income"),
	"deduction_income_account": ("root_type", "Income"),
	"receivable_account": ("account_type", "Receivable"),
	"tenant_deposit_account": ("root_type", "Liability"),
	"maintenance_expense_account": ("root_type", "Expense"),
	"owner_payout_expense_account": ("root_type", "Expense"),
}


class RealEstateCompanyProfile(Document):
	def validate(self):
		self._block_wrong_company_links()
		self._block_rent_item_collisions()
		self._warn_account_types()
		self._warn_discriminator_drift()

	# The resolver request-caches profiles on frappe.local; any profile write in the SAME
	# process (bench console, migration patch, test runner) must drop that cache or the
	# resolver would keep serving pre-write values — on a money path.
	def on_update(self):
		frappe.local.__dict__.pop("_bnd_company_profiles", None)

	def on_trash(self):
		frappe.local.__dict__.pop("_bnd_company_profiles", None)

	def after_rename(self, old, new, merge=False):
		frappe.local.__dict__.pop("_bnd_company_profiles", None)

	def _block_wrong_company_links(self):
		meta = frappe.get_meta(self.doctype)
		for f in _ACCOUNT_FIELDS:
			acc = self.get(f)
			if not acc:
				continue
			row = frappe.db.get_value("Account", acc, ["company", "is_group"], as_dict=True)
			label = _(meta.get_label(f))
			if not row:
				frappe.throw(_("{0}: Account {1} does not exist.").format(label, acc))
			if row.is_group:
				frappe.throw(_("{0}: {1} is a group account — pick a ledger account.").format(label, acc))
			if row.company != self.company:
				frappe.throw(
					_("{0}: Account {1} belongs to company {2}, not {3}.").format(
						label, acc, row.company, self.company
					)
				)
		if self.default_cost_center:
			row = frappe.db.get_value(
				"Cost Center", self.default_cost_center, ["company", "is_group"], as_dict=True
			)
			if row and (row.is_group or row.company != self.company):
				frappe.throw(
					_("Default Cost Center {0} must be a non-group cost center of company {1}.").format(
						self.default_cost_center, self.company
					)
				)
		for f in ("commercial_tax_template", "residential_tax_template"):
			tpl = self.get(f)
			if tpl:
				tpl_company = frappe.db.get_value("Sales Taxes and Charges Template", tpl, "company")
				if tpl_company and tpl_company != self.company:
					frappe.throw(
						_("{0}: template {1} belongs to company {2}, not {3}.").format(
							_(frappe.get_meta(self.doctype).get_label(f)), tpl, tpl_company, self.company
						)
					)

	def _block_rent_item_collisions(self):
		"""The rent item is the cash-basis owner-payout discriminator — it must never double
		as a Charge Type service item (save-time mirror of the runtime guard in charge_engine)."""
		if not self.default_rent_item:
			return
		ct = frappe.db.get_value("Charge Type", {"item": self.default_rent_item}, "name")
		if ct:
			frappe.throw(
				_(
					"Default Rent Item {0} is also the Service Item of Charge Type {1} — charge cash "
					"would be counted as rent in the owner payout. Use a dedicated rent item."
				).format(self.default_rent_item, ct)
			)

	def _warn_account_types(self):
		for f, (attr, expected) in _ACCOUNT_EXPECT.items():
			acc = self.get(f)
			if not acc:
				continue
			actual = frappe.db.get_value("Account", acc, attr)
			if actual and actual != expected:
				frappe.msgprint(
					_("{0}: {1} is {2} “{3}” (expected {4}) — double-check the account.").format(
						_(frappe.get_meta(self.doctype).get_label(f)), acc, attr, actual, expected
					),
					indicator="orange",
				)

	def _warn_discriminator_drift(self):
		"""Changing the rent income account / rent item while submitted rent invoices exist
		shifts the cash-basis payout discriminator — allowed, but never silently."""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		changed = [
			f for f in ("rent_income_account", "default_rent_item")
			if before.get(f) and before.get(f) != self.get(f)
		]
		if not changed:
			return
		has_invoices = frappe.db.exists(
			"Sales Invoice", {"company": self.company, "docstatus": 1, "is_return": 0}
		)
		if has_invoices:
			frappe.msgprint(
				_(
					"You changed {0} while submitted invoices exist for {1}. The cash-basis owner "
					"payout identifies rent by these values — past periods keep the OLD ones, so "
					"review any open payout windows before the next run."
				).format(", ".join(changed), self.company),
				indicator="orange",
			)
