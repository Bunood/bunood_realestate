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
