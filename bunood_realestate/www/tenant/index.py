# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

from bunood_realestate.portal import customers_for_user


def get_context(context):
	context.no_cache = 1

	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/tenant"
		raise frappe.Redirect

	customers = customers_for_user()
	context.linked = bool(customers)
	context.leases = []
	context.invoices = []
	context.outstanding = 0.0

	if not customers:
		return context

	context.leases = frappe.get_all(
		"Lease Contract",
		filters={"customer": ["in", customers], "docstatus": 1},
		fields=["name", "property", "start_date", "end_date", "status", "annual_rent_total"],
		order_by="start_date desc",
	)
	context.invoices = frappe.get_all(
		"Sales Invoice",
		filters={"customer": ["in", customers], "docstatus": 1},
		fields=["name", "posting_date", "due_date", "grand_total", "outstanding_amount", "status"],
		order_by="posting_date desc",
		limit=50,
	)
	context.outstanding = sum(flt(i.outstanding_amount) for i in context.invoices)
	context.has_active_lease = any(l.status == "Active" for l in context.leases)

	context.maintenance = frappe.get_all(
		"Maintenance Request",
		filters={"tenant": ["in", customers]},
		fields=["name", "subject", "status", "priority", "reported_on"],
		order_by="reported_on desc",
		limit=20,
	)
	return context
