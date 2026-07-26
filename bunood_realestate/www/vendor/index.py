# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe

from bunood_realestate.portal import suppliers_for_user


def get_context(context):
	context.no_cache = 1

	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/vendor"
		raise frappe.Redirect

	suppliers = suppliers_for_user()
	context.linked = bool(suppliers)
	context.work_orders = []

	if not suppliers:
		return context

	# Scoped to the contractor's own Supplier(s) — never another vendor's jobs.
	context.work_orders = frappe.get_all(
		"Maintenance Work Order",
		filters={"contractor": ["in", suppliers]},
		fields=["name", "maintenance_request", "property", "unit", "status", "scheduled_date", "notes"],
		order_by="scheduled_date desc",
		limit=100,
	)
	return context
