# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""bunood_realestate.core.charge — the Charge engine.

Event-driven, decoupled from accounting:

    <event>  ->  _apply(...)  ->  Charge (Pending)
                                    |
                     post_reference_charges(...)
                                    v
                        ERPNext Sales Invoice (accounting)

A Charge is a neutral money obligation. Today it becomes a Sales Invoice; tomorrow
the same Charge could become a receipt / Journal Entry / notification. We NEVER post
GL ourselves; ERPNext does. The Charge carries its own tax_template so the CALLER
(which knows residential vs commercial) sets VAT correctly — the engine just applies it.
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
	tax_template=None,
):
	"""Public API — enforces Charge-create permission, then raises the Charge."""
	frappe.has_permission("Charge", "create", throw=True)
	return _apply(
		charge_type, party, party_type, amount, company,
		reference_doctype, reference_name, posting_date, remarks, tax_template,
	)


def _apply(
	charge_type,
	party,
	party_type="Customer",
	amount=None,
	company=None,
	reference_doctype=None,
	reference_name=None,
	posting_date=None,
	remarks=None,
	tax_template=None,
):
	"""Internal — raise a Charge (Pending). Called from trusted server hooks that run as
	a user who may lack direct Charge-create rights, so it does NOT check permissions."""
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
			"tax_template": tax_template,
			"status": "Pending",
		}
	)
	charge.insert(ignore_permissions=True)
	return charge.name


@frappe.whitelist()
def post_reference_charges(reference_doctype, reference_name, posting_date=None):
	"""Turn Pending charges of a source document into submitted Sales Invoice(s) —
	one per (party_type, party, company). Requires Sales-Invoice submit rights."""
	frappe.has_permission("Sales Invoice", "submit", throw=True)
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
	# Row lock + re-validate so concurrent posters serialize (no double-invoicing).
	locked = frappe.db.get_values(
		"Charge", {"name": ["in", charge_names], "status": "Pending"}, "name", for_update=True
	)
	pending = [r[0] for r in locked]
	if not pending:
		return None
	if party_type != "Customer":
		frappe.throw(_("Only Customer charges can be posted to a Sales Invoice for now."))
	charges = [frappe.get_doc("Charge", n) for n in pending]

	si = frappe.new_doc("Sales Invoice")
	si.customer = party
	si.company = company
	# Pin to company currency — Charge.amount is company-denominated.
	si.currency = frappe.get_cached_value("Company", company, "default_currency")
	si.conversion_rate = 1
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

	# VAT: apply the tax template the CALLER attached to the charges (residential exempt /
	# commercial 15%). Without it, a taxable charge would post a non-compliant 0-VAT invoice.
	template = next((c.tax_template for c in charges if c.tax_template), None)
	if template:
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		si.taxes_and_charges = template
		for tax in get_taxes_and_charges("Sales Taxes and Charges Template", template):
			si.append("taxes", tax)

	si.flags.ignore_permissions = True
	si.insert()
	si.submit()

	for c in charges:
		frappe.db.set_value("Charge", c.name, {"status": "Invoiced", "sales_invoice": si.name})
	return si.name
