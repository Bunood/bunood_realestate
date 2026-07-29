# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Phase 5 — keep Rent Schedule rows in step with their Sales Invoice, without a
parallel ledger. We only mirror the invoice's status onto the row for display;
the source of truth for money stays ERPNext (GL / AR / Payment Ledger)."""

import frappe
from frappe import _


def sync_rent_schedule_on_invoice(doc, method=None):
	"""Sales Invoice doc_event. On cancel/delete, free the period to be re-invoiced."""
	if method in ("on_cancel", "on_trash"):
		_block_cancel_with_live_credit(doc)
		_revert_schedule_rows(doc.name)
	else:
		_sync_invoice_status(doc.name, doc.status)


def _block_cancel_with_live_credit(doc):
	"""A termination credit note is STANDALONE (no return_against), so ERPNext itself would
	happily cancel the original rent invoice underneath it — leaving a live credit against a
	voided invoice, a Planned-again period, and a broken re-credit guard. Restore the
	protection return_against would have given: block the cancel until the credit note (or
	the termination) is cancelled first."""
	if doc.get("is_return"):
		return
	live = frappe.db.sql(
		"""
		SELECT cn.name
		FROM `tabLease Termination Credit` ltc
		JOIN `tabSales Invoice` cn ON cn.name = ltc.credit_note
		WHERE ltc.sales_invoice = %s AND cn.docstatus = 1
		LIMIT 1
		""",
		doc.name,
	)
	if live:
		frappe.throw(
			_(
				"Credit Note {0} (lease termination) is issued against this invoice — cancel it first."
			).format(live[0][0])
		)


def sync_rent_schedule_on_payment(doc, method=None):
	"""Payment Entry doc_event (submit/cancel): re-read each referenced invoice status."""
	for ref in doc.references or []:
		if ref.reference_doctype == "Sales Invoice" and ref.reference_name:
			_sync_invoice_status(ref.reference_name)


def _sync_invoice_status(si_name, status=None):
	rows = frappe.get_all("Rent Schedule", filters={"sales_invoice": si_name}, pluck="name")
	if not rows:
		return
	if status is None:
		status = frappe.db.get_value("Sales Invoice", si_name, "status")
	for name in rows:
		frappe.db.set_value("Rent Schedule", name, "invoice_status", status, update_modified=False)


def _revert_schedule_rows(si_name):
	"""Invoice cancelled → clear the link and reset the row to Planned so the daily
	generator can re-invoice the period (avoids bunood_core's stuck-period revenue leak)."""
	rows = frappe.get_all("Rent Schedule", filters={"sales_invoice": si_name}, pluck="name")
	for name in rows:
		frappe.db.set_value(
			"Rent Schedule",
			name,
			{"sales_invoice": None, "status": "Planned", "invoice_status": None},
			update_modified=False,
		)


def reconcile_owner_payout_on_je(doc, method=None):
	"""Journal Entry ON_CANCEL doc_event. If this JE backs a Posted Owner Payout, mark that
	payout Cancelled so it stops acting as an idempotency block — the GL was just reversed, so
	a re-generated period SHOULD post afresh. The journal_entry LINK is deliberately kept (not
	nulled) so that if the operator AMENDS this JE, ``relink_owner_payout_on_je_amend`` can
	re-attach the payout to the replacement (else amend would re-credit the owner in the GL
	with no Posted payout guarding it → the double-pay hole)."""
	payout = frappe.db.get_value(
		"Owner Payout", {"journal_entry": doc.name, "status": "Posted"}, "name"
	)
	if payout:
		frappe.db.set_value("Owner Payout", payout, "status", "Cancelled", update_modified=False)


def relink_owner_payout_on_je_amend(doc, method=None):
	"""Journal Entry ON_SUBMIT doc_event. ERPNext "amend" = cancel the original JE then submit
	a fresh copy carrying ``amended_from``. When that replacement is an owner-payout JE, the
	owner credit is live again in the GL — so re-attach the (now-Cancelled) Owner Payout to the
	new JE and restore it to Posted, keeping exactly one Posted payout per live credit. Without
	this, re-running generate_owner_payout for the same window sees no Posted payout and pays
	the owner a SECOND time."""
	if not doc.amended_from:
		return
	payout = frappe.db.get_value("Owner Payout", {"journal_entry": doc.amended_from}, "name")
	if payout:
		frappe.db.set_value(
			"Owner Payout", payout, {"journal_entry": doc.name, "status": "Posted"}, update_modified=False
		)


def owner_payout_unlink_on_je_trash(doc, method=None):
	"""Journal Entry ON_TRASH doc_event. Null any Owner Payout link to this JE so deleting the
	(already-cancelled) JE is not blocked by LinkExistsError — mirror of the deposit mirror.
	Status is untouched (a cancelled payout stays Cancelled)."""
	payout = frappe.db.get_value("Owner Payout", {"journal_entry": doc.name}, "name")
	if payout:
		frappe.db.set_value("Owner Payout", payout, "journal_entry", None, update_modified=False)


def reset_work_order_on_pi_cancel(doc, method=None):
	"""Purchase Invoice cancel/trash doc_event. If this PI was a Maintenance Work Order's
	contractor bill, clear the work order's ``purchase_invoice`` link so a corrected bill can
	be re-posted (mirror of the rent ``_revert_schedule_rows`` reset-on-cancel discipline).
	Without this the idempotency guard, which only tests link non-emptiness, would refuse to
	re-bill forever after an operator cancels a wrong-amount PI."""
	wo = frappe.db.get_value("Maintenance Work Order", {"purchase_invoice": doc.name}, "name")
	if wo:
		frappe.db.set_value("Maintenance Work Order", wo, "purchase_invoice", None, update_modified=False)


def flag_owner_payout_on_payment_cancel(doc, method=None):
	"""Payment Entry on_cancel doc_event. Cash-basis owner payouts pay a share of COLLECTED
	rent; if a settling payment is later cancelled (bounced cheque / reversal), a payout
	already Posted for that property+period was over-paid. Auto-reversing is an operator
	decision (the clawback usually nets against the next payout), so we do NOT post anything —
	but we make the discrepancy VISIBLE with a timeline comment on each affected Posted payout,
	so it is never silent. Best-effort: a comment failure must never block the PE cancellation."""
	try:
		props = set()
		for ref in (doc.references or []):
			if ref.reference_doctype == "Sales Invoice" and ref.reference_name:
				prop = frappe.db.get_value("Sales Invoice", ref.reference_name, "property")
				if prop:
					props.add(prop)
		if not props:
			return
		pay_date = doc.posting_date
		for prop in props:
			for name in frappe.get_all(
				"Owner Payout",
				filters={
					"property": prop, "status": "Posted",
					"from_date": ["<=", pay_date], "to_date": [">=", pay_date],
				},
				pluck="name",
			):
				frappe.get_doc("Owner Payout", name).add_comment(
					"Comment",
					_(
						"Settling Payment Entry {0} covering this period was cancelled — collected "
						"rent fell, so this owner payout may be over-paid. Review and adjust the next payout."
					).format(doc.name),
				)
	except Exception:
		frappe.log_error(title="Bunood: owner-payout cancel-flag failed", message=frappe.get_traceback())


def reconcile_deposit_on_je(doc, method=None):
	"""Journal Entry cancel/trash doc_event. The lease caches deposit_received /
	deposit_refunded as a convenience mirror of the deposit JEs; if an operator cancels
	the underlying JE in ERPNext, that mirror must follow the GL — otherwise the app
	would let a tenant be refunded/settled against a liability that no longer exists
	(exactly bunood_core's parallel-ledger divergence). Keep the mirror = the GL."""
	from frappe.utils import flt

	# The cancelled JE was a lease's recorded DEPOSIT RECEIPT → the deposit is gone.
	lease = frappe.db.get_value("Lease Contract", {"deposit_journal_entry": doc.name}, "name")
	if lease:
		frappe.db.set_value(
			"Lease Contract",
			lease,
			{"deposit_received": 0, "deposit_received_date": None, "deposit_journal_entry": None},
		)

	# The cancelled JE was a lease's recorded REFUND → the refund is undone (held goes back up).
	lease_r = frappe.db.get_value("Lease Contract", {"deposit_refund_journal_entry": doc.name}, "name")
	if lease_r:
		refunded = flt(frappe.db.get_value("Lease Contract", lease_r, "deposit_refunded"))
		amt = flt(getattr(doc, "total_debit", 0)) or flt(getattr(doc, "total_credit", 0))
		frappe.db.set_value(
			"Lease Contract",
			lease_r,
			{"deposit_refunded": max(0.0, refunded - amt), "deposit_refund_journal_entry": None},
		)
