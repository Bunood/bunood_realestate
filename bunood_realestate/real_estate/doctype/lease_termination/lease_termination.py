# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class LeaseTermination(Document):
	def validate(self):
		lease = frappe.get_doc("Lease Contract", self.lease_contract)
		self.deposit_held = flt(lease.deposit_received) - flt(lease.deposit_refunded)
		self.total_deductions = sum(flt(d.amount) for d in self.deductions)
		self.net_refund = flt(self.deposit_held) - flt(self.total_deductions)

		if self.net_refund < 0:
			frappe.throw(_("Total deductions exceed the held deposit. Handle the excess as a separate charge."))
		if flt(self.net_refund) > 0 and not self.refund_account:
			frappe.throw(_("Set the Refund account (net refund is {0}).").format(self.net_refund))
		for d in self.deductions:
			if not d.income_account:
				frappe.throw(_("Each deduction needs an Income Account."))

	def on_submit(self):
		self._cancel_future_rent()
		self._post_settlement()
		self._close_lease()
		self.db_set("status", "Settled")

	def on_cancel(self):
		if self.refund_journal_entry:
			je = frappe.get_doc("Journal Entry", self.refund_journal_entry)
			if je.docstatus == 1:
				je.cancel()
		lease = frappe.get_doc("Lease Contract", self.lease_contract)
		lease.db_set("deposit_refunded", flt(lease.deposit_refunded) - flt(self.deposit_held))
		lease.db_set("status", "Active")
		# Re-occupy the units (symmetric to _close_lease) — otherwise the lease is Active
		# again while its units read Vacant, giving a wrong occupancy KPI and re-offering
		# a still-leased unit in the wizard.
		for row in lease.units:
			if row.unit:
				frappe.db.set_value(
					"Real Estate Unit", row.unit, {"status": "Occupied", "current_lease": lease.name}
				)
		self._restore_future_rent()
		self.db_set("status", "Cancelled")

	def _restore_future_rent(self):
		"""Reactivating the lease: re-plan the rent rows this termination had cancelled."""
		rows = frappe.get_all(
			"Rent Schedule",
			filters={
				"lease_contract": self.lease_contract,
				"status": "Cancelled",
				"sales_invoice": ["in", [None, ""]],
				"due_date": [">=", self.termination_date],
			},
			pluck="name",
		)
		for name in rows:
			frappe.db.set_value("Rent Schedule", name, "status", "Planned")

	def _cancel_future_rent(self):
		"""Cancel still-Planned schedule rows due on/after the termination date."""
		rows = frappe.get_all(
			"Rent Schedule",
			filters={
				"lease_contract": self.lease_contract,
				"status": "Planned",
				"due_date": [">=", self.termination_date],
			},
			pluck="name",
		)
		for name in rows:
			frappe.db.set_value("Rent Schedule", name, "status", "Cancelled")

	def _post_settlement(self):
		"""DR deposit liability (held) / CR refund (net) / CR each deduction income."""
		if flt(self.deposit_held) <= 0:
			return
		settings = frappe.get_single("Real Estate Settings")
		if not settings.tenant_deposit_account:
			frappe.throw(_("Set the Tenant Security Deposit Account in Real Estate Settings."))

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = self.termination_date
		je.user_remark = _("Deposit settlement — Lease {0}").format(self.lease_contract)
		je.append("accounts", {
			"account": settings.tenant_deposit_account,
			"debit_in_account_currency": flt(self.deposit_held),
		})
		if flt(self.net_refund) > 0:
			je.append("accounts", {
				"account": self.refund_account,
				"credit_in_account_currency": flt(self.net_refund),
			})
		for d in self.deductions:
			if flt(d.amount) > 0:
				# Deduction accounts are income (P&L) → a cost center is required for GL.
				line = {"account": d.income_account, "credit_in_account_currency": flt(d.amount)}
				if settings.default_cost_center:
					line["cost_center"] = settings.default_cost_center
				je.append("accounts", line)
		je.flags.ignore_permissions = True
		je.insert()
		je.submit()
		self.db_set("refund_journal_entry", je.name)

	def _close_lease(self):
		lease = frappe.get_doc("Lease Contract", self.lease_contract)
		# The whole held deposit is now settled (net refunded + deductions recognised).
		lease.db_set("deposit_refunded", flt(lease.deposit_refunded) + flt(self.deposit_held))
		lease.db_set("status", "Expired")
		for row in lease.units:
			if row.unit:
				frappe.db.set_value(
					"Real Estate Unit", row.unit, {"status": "Vacant", "current_lease": None}
				)
