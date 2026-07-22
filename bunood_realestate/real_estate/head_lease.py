# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Master-Lease (استثماري) payable side: the rent the company PAYS the original owner.

property head-lease terms  ->  Head Lease Schedule (Planned)
                                     |
                        post due bills (daily / button)
                                     v
                        ERPNext Purchase Invoice (to the owner Supplier)

Profit = sub-lease rent income (Rent Schedule) − head-lease expense (here), visible in
ERPNext P&L by the Property accounting dimension. We never post GL ourselves.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate

from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import build_periods


def generate_for_property(prop):
	"""Build the head-lease payment schedule from the property's head-lease terms. Idempotent."""
	p = frappe.get_doc("Property", prop) if isinstance(prop, str) else prop
	if p.operation_type != "Master Lease":
		frappe.throw(_("A head-lease schedule applies only to Master Lease properties."))
	if not (
		p.head_landlord_party
		and flt(p.head_lease_annual_rent)
		and p.head_lease_start_date
		and p.head_lease_end_date
		and p.head_lease_billing_cycle
	):
		frappe.throw(_("Set head landlord, annual rent, start/end dates and billing cycle first."))
	if frappe.db.exists("Head Lease Schedule", {"property": p.name}):
		return 0

	periods = build_periods(
		p.head_lease_start_date, p.head_lease_end_date, p.head_lease_billing_cycle, p.head_lease_annual_rent
	)
	for per in periods:
		frappe.get_doc(
			{
				"doctype": "Head Lease Schedule",
				"property": p.name,
				"head_landlord": p.head_landlord_party,
				"company": p.company,
				"period_no": per["period_no"],
				"period_start": per["period_start"],
				"period_end": per["period_end"],
				"due_date": per["period_start"],
				"amount": per["base_amount"],
				"is_prorated": per["is_prorated"],
				"status": "Planned",
			}
		).insert(ignore_permissions=True)
	return len(periods)


@frappe.whitelist()
def generate_now(property):
	frappe.has_permission("Head Lease Schedule", "create", throw=True)
	return generate_for_property(property)


def generate_due_head_lease_bills(property=None, lead_days=None):
	"""Scheduler/manual: due Planned rows -> submitted Purchase Invoice to the owner."""
	settings = frappe.get_single("Real Estate Settings")
	if lead_days is None:
		lead_days = int(settings.invoice_lead_days or 0)
	cutoff = add_days(nowdate(), lead_days)

	filters = {"status": "Planned", "purchase_invoice": ["in", [None, ""]], "due_date": ["<=", cutoff]}
	if property:
		filters["property"] = property
	names = frappe.get_all("Head Lease Schedule", filters=filters, order_by="due_date asc", pluck="name")

	created = 0
	for name in names:
		try:
			if _post_bill(name, settings):
				created += 1
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: head-lease bill generation failed",
				message=f"Head Lease Schedule {name}\n\n{frappe.get_traceback()}",
			)
			frappe.db.set_value(
				"Head Lease Schedule", name, {"status": "Failed", "invoice_status": str(e)[:140]},
				update_modified=False,
			)
			frappe.db.commit()
	return created


def _post_bill(schedule_name, settings=None):
	settings = settings or frappe.get_single("Real Estate Settings")
	# Row lock + re-validate (no double-billing under concurrency).
	frappe.db.get_value("Head Lease Schedule", schedule_name, "name", for_update=True)
	row = frappe.get_doc("Head Lease Schedule", schedule_name)
	if row.status != "Planned" or row.purchase_invoice:
		return False
	if not settings.head_lease_item:
		frappe.throw(_("Set a Head-Lease Item in Real Estate Settings."))
	if not row.head_landlord:
		frappe.throw(_("Head Lease Schedule {0} has no head landlord.").format(row.name))

	pi = frappe.new_doc("Purchase Invoice")
	pi.supplier = row.head_landlord
	pi.company = row.company
	pi.currency = frappe.get_cached_value("Company", row.company, "default_currency")
	pi.conversion_rate = 1
	pi.set_posting_time = 1
	pi.posting_date = row.due_date
	pi.bill_no = f"HL-{row.name}"
	pi.bill_date = row.due_date
	item = pi.append("items", {})
	item.item_code = settings.head_lease_item
	item.qty = 1
	item.rate = flt(row.amount)
	item.description = _("Head-lease {0} ({1} to {2})").format(row.property, row.period_start, row.period_end)

	pi.flags.ignore_permissions = True
	pi.insert()
	pi.submit()

	frappe.db.set_value(
		"Head Lease Schedule", row.name,
		{"purchase_invoice": pi.name, "status": "Invoiced", "invoice_status": pi.status},
	)
	return True


@frappe.whitelist()
def post_due_bills(property=None):
	frappe.has_permission("Purchase Invoice", "submit", throw=True)
	return generate_due_head_lease_bills(property=property)
