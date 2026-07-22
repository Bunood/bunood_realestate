# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Phase 5 — security deposits as a LIABILITY via native Journal Entries.

Receipt:  DR Bank/Cash            / CR Tenant Security Deposit (liability)
Refund:   DR Tenant Security Deposit / CR Bank/Cash
Held as a liability until settled. Full move-out inspection + deductions is Phase 6.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate


@frappe.whitelist()
def record_deposit(lease_contract, amount, paid_to_account, posting_date=None):
	frappe.only_for(["Accounts Manager", "System Manager"])
	settings = frappe.get_single("Real Estate Settings")
	if not settings.tenant_deposit_account:
		frappe.throw(_("Set the Tenant Security Deposit Account in Real Estate Settings."))

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Deposit amount must be greater than zero."))

	# Serialize concurrent deposit actions on this lease, then re-read the committed
	# state under the lock so a double-click / two operators can't both post a receipt.
	frappe.db.get_value("Lease Contract", lease_contract, "name", for_update=True)
	state = frappe.db.get_value(
		"Lease Contract", lease_contract, ["company", "deposit_received"], as_dict=True
	)
	if not state:
		frappe.throw(_("Lease {0} not found.").format(lease_contract))
	if state.deposit_received:
		frappe.throw(_("A deposit has already been recorded for this lease."))

	je = _make_journal(
		company=state.company,
		posting_date=posting_date or nowdate(),
		debit_account=paid_to_account,
		credit_account=settings.tenant_deposit_account,
		amount=amount,
		remark=_("Security deposit received — Lease {0}").format(lease_contract),
	)
	frappe.db.set_value(
		"Lease Contract",
		lease_contract,
		{
			"deposit_received": amount,
			"deposit_received_date": je.posting_date,
			"deposit_journal_entry": je.name,
		},
	)
	return je.name


@frappe.whitelist()
def refund_deposit(lease_contract, amount, paid_from_account, posting_date=None):
	frappe.only_for(["Accounts Manager", "System Manager"])
	settings = frappe.get_single("Real Estate Settings")
	if not settings.tenant_deposit_account:
		frappe.throw(_("Set the Tenant Security Deposit Account in Real Estate Settings."))

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Refund amount must be greater than zero."))

	# Serialize concurrent refunds on this lease, then re-read the held balance under
	# the lock so two refunds can't each pass a stale balance check and double-pay cash.
	frappe.db.get_value("Lease Contract", lease_contract, "name", for_update=True)
	state = frappe.db.get_value(
		"Lease Contract", lease_contract, ["company", "deposit_received", "deposit_refunded"], as_dict=True
	)
	if not state:
		frappe.throw(_("Lease {0} not found.").format(lease_contract))
	held = flt(state.deposit_received) - flt(state.deposit_refunded)
	if amount > held:
		frappe.throw(_("Refund exceeds the held deposit balance."))

	je = _make_journal(
		company=state.company,
		posting_date=posting_date or nowdate(),
		debit_account=settings.tenant_deposit_account,
		credit_account=paid_from_account,
		amount=amount,
		remark=_("Security deposit refund — Lease {0}").format(lease_contract),
	)
	frappe.db.set_value(
		"Lease Contract",
		lease_contract,
		{
			"deposit_refunded": flt(state.deposit_refunded) + amount,
			"deposit_refund_journal_entry": je.name,
		},
	)
	return je.name


def _make_journal(company, posting_date, debit_account, credit_account, amount, remark):
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = posting_date
	je.user_remark = remark
	je.append("accounts", {"account": debit_account, "debit_in_account_currency": amount})
	je.append("accounts", {"account": credit_account, "credit_in_account_currency": amount})
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je
