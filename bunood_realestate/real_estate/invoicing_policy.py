# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Invoicing issuance policy — the SINGLE choke point deciding WHEN a tenant-facing
tax invoice comes into existence (docs/plan-invoicing-automation.md).

Why one policy instead of the old `auto_submit_invoices` + `invoice_lead_days` pair:
issuance is a TAX decision, not a technical toggle. Once a Sales Invoice is submitted,
ksa_compliance reports/clears it to ZATCA and it can no longer be cancelled — only
credited. So the operator must choose one clear behavior, not reason about two flags.

    Manual          — nothing auto-issues; the Operations Center issues on demand.
    On Due Date     — the daily generator issues on the due date.
    Days Before Due — same, `invoice_lead_days` early.
    On Payment      — nothing auto-issues; «استلام الدفعة» issues at collection time
                      (residential-friendly: the VAT liability starts when cash arrives).

Governs the TENANT-FACING generators (rent + charges). Head-lease PURCHASE invoices are
the owner's tax document, not ours, so they keep their own lead-day behavior untouched.

Issuance ALWAYS submits: a draft invoice is not a tax document — it reaches neither the
GL nor ZATCA — so "issued but draft" is a state with no business meaning here. The old
`auto_submit_invoices = 0` (draft-until-paid) is expressed far better by `On Payment`.
"""

import frappe
from frappe.utils import cint

MANUAL = "Manual"
ON_DUE_DATE = "On Due Date"
DAYS_BEFORE_DUE = "Days Before Due"
ON_PAYMENT = "On Payment"

POLICIES = (MANUAL, ON_DUE_DATE, DAYS_BEFORE_DUE, ON_PAYMENT)

# Policies under which the DAILY generators may create invoices by themselves.
AUTO_POLICIES = (ON_DUE_DATE, DAYS_BEFORE_DUE)

DEFAULT_DUE_SOON_DAYS = 5


def resolve(policy, lead_days):
	"""Pure & testable: (stored policy, stored lead days) → (policy, effective lead days).

	An unknown/blank policy resolves to `Manual` — the FAIL-SAFE direction. Issuance now
	always submits, and a submitted invoice is reported to ZATCA and can never be
	cancelled, only credited. So on a half-migrated or misconfigured site the harmless
	failure is "issue nothing and let the Operations Center show the backlog"; auto-issuing
	irreversible tax documents nobody asked for is not recoverable."""
	policy = (policy or "").strip()
	if policy not in POLICIES:
		policy = MANUAL
	if policy == DAYS_BEFORE_DUE:
		return policy, max(0, cint(lead_days))
	# Manual / On Payment never auto-issue, so their lead window is meaningless.
	return policy, 0


def auto_issues(policy):
	"""Does this policy let the DAILY generator create invoices without a human?"""
	return policy in AUTO_POLICIES


def current(settings=None):
	"""(policy, effective lead days) from site-wide settings. Site-wide by design:
	issuance policy is a GLOBAL_FIELD (see company_settings), never per-company."""
	if settings is not None:
		return resolve(settings.get("invoice_issuance_policy"), settings.get("invoice_lead_days"))
	return resolve(
		frappe.db.get_single_value("Real Estate Settings", "invoice_issuance_policy"),
		frappe.db.get_single_value("Real Estate Settings", "invoice_lead_days"),
	)


def due_soon_days():
	"""How many days ahead an unissued installment turns 🟠 'مستحق قريباً'."""
	value = frappe.db.get_single_value("Real Estate Settings", "due_soon_days")
	return cint(value) if value else DEFAULT_DUE_SOON_DAYS
