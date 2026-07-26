# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

from bunood_realestate.portal import suppliers_for_user


def get_context(context):
	context.no_cache = 1

	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/owner"
		raise frappe.Redirect

	suppliers = suppliers_for_user()
	context.linked = bool(suppliers)
	context.properties = []
	context.payouts = []
	context.total_paid = 0.0

	if not suppliers:
		return context

	# Everything scoped to the owner's own Supplier(s) — never another owner's data.
	context.properties = frappe.get_all(
		"Property",
		filters={"owner_party": ["in", suppliers]},
		fields=["name", "property_name", "management_behavior", "management_fee_percentage"],
		order_by="property_name asc",
	)
	context.payouts = frappe.get_all(
		"Owner Payout",
		filters={"owner_party": ["in", suppliers], "status": "Posted"},
		fields=["name", "property", "from_date", "to_date", "rent_base", "fee_amount", "owner_payout"],
		order_by="from_date desc",
		limit=100,
	)
	context.total_paid = sum(flt(p.owner_payout) for p in context.payouts)
	return context
