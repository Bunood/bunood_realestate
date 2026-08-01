# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Fold `auto_submit_invoices` + `invoice_lead_days` into `invoice_issuance_policy`
(docs/plan-invoicing-automation.md §1) WITHOUT changing what any live site does.

    auto_submit_invoices = 1, lead = 0   → On Due Date      (today's behavior)
    auto_submit_invoices = 1, lead > 0   → Days Before Due  (today's behavior)
    auto_submit_invoices = 0             → Manual

Why 0 → Manual (not "issue a draft"): the old flag produced a DRAFT invoice on the due
date — no GL, no ZATCA, no receivable. Nothing of business value is lost by not creating
it, and the installment stays visible in the Operations Center. Issuing-then-submitting
against the operator's old choice would be the unsafe direction (an irreversible ZATCA
document), so we never do that.

A site with no real-estate data yet keeps the doctype default (Manual) — it has no
behavior to preserve, and Manual is the agreed product default.
"""

import frappe
from frappe.utils import cint

from bunood_realestate.real_estate.invoicing_policy import (
	DAYS_BEFORE_DUE,
	MANUAL,
	ON_DUE_DATE,
	POLICIES,
)


def _single(field):
	"""Read a Single's stored value WITHOUT going through the DocType meta.

	`frappe.db.get_single_value` resolves the field against `frappe.get_meta(...)` and
	`frappe.throw`s "Field X does not exist" when it is absent — and this patch must read
	`auto_submit_invoices`, which post_model_sync has ALREADY removed from the DocType.
	Going straight at `tabSingles` (where the value still lives) is the only safe read;
	the meta-bound helper would abort `bench migrate` on every upgrading site."""
	rows = frappe.db.get_all(
		"Singles",
		filters={"doctype": "Real Estate Settings", "field": field},
		pluck="value",
		limit=1,
	)
	return rows[0] if rows else None


def execute():
	if not frappe.db.exists("DocType", "Real Estate Settings"):
		return

	# Idempotent: never overwrite a policy an operator has already chosen.
	current = _single("invoice_issuance_policy")
	if current in POLICIES:
		return

	auto = _single("auto_submit_invoices")
	lead = cint(_single("invoice_lead_days"))

	has_history = bool(
		frappe.db.exists("Rent Schedule", {"name": ["is", "set"]})
		or frappe.db.exists("Lease Contract", {"docstatus": 1})
	)
	if not has_history:
		policy = MANUAL
	elif cint(auto) if auto is not None else 1:
		# auto flag on (or never saved — its JSON default was 1, so that WAS the behavior)
		policy = DAYS_BEFORE_DUE if lead > 0 else ON_DUE_DATE
	else:
		policy = MANUAL

	frappe.db.set_single_value("Real Estate Settings", "invoice_issuance_policy", policy)
	# `invoice_lead_days` is NOT cleared: head-lease PURCHASE invoice generation reads the
	# same field (head_lease.py), and this patch promises to change no behavior. Zeroing it
	# to tidy the UI would silently shift when owners get billed.
	if not _single("due_soon_days"):
		frappe.db.set_single_value("Real Estate Settings", "due_soon_days", 5)
	frappe.db.commit()
