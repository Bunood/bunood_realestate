# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Tenant portal helpers. Every query is scoped to the Customer(s) linked to the
logged-in portal user (via Contact → Dynamic Link) — a tenant can only ever see
their OWN leases, invoices and dues. Guests get nothing."""

import frappe
from frappe import _


def customers_for_user(user=None):
	"""The Customer(s) the logged-in portal user is linked to (via their Contact).
	Returns [] for Guest or an unlinked user — callers MUST treat [] as 'no access'."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return []
	contacts = frappe.get_all("Contact", filters={"user": user}, pluck="name")
	if not contacts:
		return []
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": ["in", contacts], "link_doctype": "Customer"},
		pluck="link_name",
	)
	return sorted(set(filter(None, links)))


def _require_tenant():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in to access the tenant portal."), frappe.PermissionError)
	customers = customers_for_user()
	if not customers:
		frappe.throw(_("Your account is not linked to a tenant."), frappe.PermissionError)
	return customers


@frappe.whitelist()
def submit_maintenance(subject, description=None, priority="Medium"):
	"""Create a Maintenance Request for the logged-in tenant's active lease.
	Scoped server-side to the tenant's own lease/property/unit — the caller cannot
	target another tenant's property."""
	customers = _require_tenant()
	if not subject or not str(subject).strip():
		frappe.throw(_("Please describe the issue."))
	if priority not in ("Low", "Medium", "High", "Urgent"):
		priority = "Medium"

	lease = frappe.get_all(
		"Lease Contract",
		filters={"customer": ["in", customers], "status": "Active", "docstatus": 1},
		fields=["name", "property", "company"],
		order_by="start_date desc",
		limit=1,
	)
	if not lease:
		frappe.throw(_("No active lease found for your account."))
	lease = lease[0]
	unit = frappe.db.get_value("Lease Unit", {"parent": lease.name}, "unit")

	doc = frappe.get_doc({
		"doctype": "Maintenance Request",
		"subject": str(subject).strip()[:140],
		"description": (description or "")[:2000],
		"priority": priority,
		"property": lease.property,
		"unit": unit,
		"lease_contract": lease.name,
		"company": lease.company,
		"status": "Open",
		"contact_phone": frappe.db.get_value("Customer", customers[0], "mobile_no"),
	})
	doc.flags.ignore_permissions = True  # portal user has no desk Maintenance perms
	doc.insert()
	return {"name": doc.name}
