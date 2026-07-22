# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""bunood_realestate.core.charge — the Charge engine.

Event-driven, decoupled from accounting:

    <something happens>  ->  charge.apply(...)  ->  Charge (Pending)
                                                      |
                                   charge.post_reference_charges(...) / post
                                                      v
                                          ERPNext Sales Invoice (accounting)

A Charge is a neutral money obligation. Today it becomes a Sales Invoice; tomorrow
the same Charge could become a receipt, a Journal Entry, or a notification — without
changing the caller. We NEVER post GL ourselves; ERPNext does.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate


@frappe.whitelist()
def apply(
	charge_type,
	party,
	party_type="Customer",
	amount=None,
	company=None,
	reference_doctype=None,
	reference_name=None,
	posting_date=None,
	remarks=None,
):
	"""Raise a Charge (status Pending). Returns the Charge name.
	`amount` defaults to the Charge Type's default_rate."""
	ct = frappe.get_cached_doc("Charge Type", charge_type)
	if not ct.is_active:
		frappe.throw(_("Charge Type {0} is not active.").format(charge_type))

	amt = flt(amount) if amount is not None else flt(ct.default_rate)
	if amt <= 0:
		frappe.throw(_("Charge amount must be greater than zero (Charge Type {0}).").format(charge_type))

	charge = frappe.get_doc(
		{
			"doctype": "Charge",
			"charge_type": charge_type,
			"party_type": party_type,
			"party": party,
			"company": company,
			"amount": amt,
			"posting_date": posting_date or nowdate(),
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"remarks": remarks,
			"status": "Pending",
		}
	)
	charge.insert(ignore_permissions=True)
	return charge.name


@frappe.whitelist()
def post_reference_charges(reference_doctype, reference_name, posting_date=None):
	"""Accounting bridge: turn all Pending charges of a source document into submitted
	Sales Invoice(s) — one per (party_type, party, company). Idempotent per charge."""
	rows = frappe.get_all(
		"Charge",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"status": "Pending",
		},
		fields=["name", "party_type", "party", "company"],
	)
	groups = {}
	for r in rows:
		groups.setdefault((r.party_type, r.party, r.company), []).append(r.name)

	invoices = []
	for (party_type, party, company), names in groups.items():
		si = _post_charges(party_type, party, company, names, posting_date)
		if si:
			invoices.append(si)
	return invoices


def _post_charges(party_type, party, company, charge_names, posting_date=None):
	charges = [frappe.get_doc("Charge", n) for n in charge_names]
	charges = [c for c in charges if c.status == "Pending"]
	if not charges:
		return None
	if party_type != "Customer":
		frappe.throw(_("Only Customer charges can be posted to a Sales Invoice for now."))

	si = frappe.new_doc("Sales Invoice")
	si.customer = party
	si.company = company
	si.set_posting_time = 1
	si.posting_date = posting_date or nowdate()

	for c in charges:
		item = frappe.db.get_value("Charge Type", c.charge_type, "item")
		if not item:
			frappe.throw(
				_("Set a Service Item on Charge Type {0} before posting its charges.").format(c.charge_type)
			)
		row = si.append("items", {})
		row.item_code = item
		row.qty = 1
		row.rate = flt(c.amount)
		row.description = c.remarks or c.charge_type

	si.flags.ignore_permissions = True
	si.insert()
	si.submit()

	for c in charges:
		frappe.db.set_value("Charge", c.name, {"status": "Invoiced", "sales_invoice": si.name})
	return si.name
