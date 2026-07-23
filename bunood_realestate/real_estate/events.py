# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Phase 5 — keep Rent Schedule rows in step with their Sales Invoice, without a
parallel ledger. We only mirror the invoice's status onto the row for display;
the source of truth for money stays ERPNext (GL / AR / Payment Ledger)."""

import frappe


def sync_rent_schedule_on_invoice(doc, method=None):
	"""Sales Invoice doc_event. On cancel/delete, free the period to be re-invoiced."""
	if method in ("on_cancel", "on_trash"):
		_revert_schedule_rows(doc.name)
	else:
		_sync_invoice_status(doc.name, doc.status)


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
