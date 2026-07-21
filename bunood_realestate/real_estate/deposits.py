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
	lease = frappe.get_doc("Lease Contract", lease_contract)
	settings = frappe.get_single("Real Estate Settings")
	if not settings.tenant_deposit_account:
		frappe.throw(_("Set the Tenant Security Deposit Account in Real Estate Settings."))

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Deposit amount must be greater than zero."))
	if lease.deposit_received:
		frappe.throw(_("A deposit has already been recorded for this lease."))

	je = _make_journal(
		company=lease.company,
		posting_date=posting_date or nowdate(),
		debit_account=paid_to_account,
		credit_account=settings.tenant_deposit_account,
		amount=amount,
		remark=_("Security deposit received — Lease {0}").format(lease.name),
	)
	lease.db_set("deposit_received", amount)
	lease.db_set("deposit_received_date", je.posting_date)
	lease.db_set("deposit_journal_entry", je.name)
	return je.name


@frappe.whitelist()
def refund_deposit(lease_contract, amount, paid_from_account, posting_date=None):
	frappe.only_for(["Accounts Manager", "System Manager"])
	lease = frappe.get_doc("Lease Contract", lease_contract)
	settings = frappe.get_single("Real Estate Settings")
	if not settings.tenant_deposit_account:
		frappe.throw(_("Set the Tenant Security Deposit Account in Real Estate Settings."))

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Refund amount must be greater than zero."))
	if amount > flt(lease.deposit_received) - flt(lease.deposit_refunded):
		frappe.throw(_("Refund exceeds the held deposit balance."))

	je = _make_journal(
		company=lease.company,
		posting_date=posting_date or nowdate(),
		debit_account=settings.tenant_deposit_account,
		credit_account=paid_from_account,
		amount=amount,
		remark=_("Security deposit refund — Lease {0}").format(lease.name),
	)
	lease.db_set("deposit_refunded", flt(lease.deposit_refunded) + amount)
	lease.db_set("deposit_refund_journal_entry", je.name)
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
