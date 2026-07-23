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


def _owned_request(request, customers):
	"""Fetch a Maintenance Request ONLY if it belongs to one of the tenant's customers,
	else raise PermissionError. Prevents a tenant from reading/posting to another
	tenant's request (IDOR). Ownership = the request's tenant/lease customer."""
	row = frappe.db.get_value(
		"Maintenance Request", request, ["name", "tenant", "lease_contract"], as_dict=True
	)
	if not row:
		frappe.throw(_("Request not found."), frappe.DoesNotExistError)
	owner_customer = row.tenant
	if not owner_customer and row.lease_contract:
		owner_customer = frappe.db.get_value("Lease Contract", row.lease_contract, "customer")
	if owner_customer not in customers:
		frappe.throw(_("You do not have access to this request."), frappe.PermissionError)
	return row.name


@frappe.whitelist()
def my_maintenance_requests():
	"""The logged-in tenant's own maintenance requests (latest first)."""
	customers = _require_tenant()
	return frappe.get_all(
		"Maintenance Request",
		filters={"tenant": ["in", customers]},
		fields=["name", "subject", "status", "priority", "reported_on", "property", "unit"],
		order_by="reported_on desc",
		limit=50,
	)


@frappe.whitelist()
def maintenance_thread(request):
	"""The conversation thread for one of the tenant's OWN requests."""
	customers = _require_tenant()
	name = _owned_request(request, customers)
	doc = frappe.get_doc("Maintenance Request", name)
	return {
		"name": doc.name,
		"subject": doc.subject,
		"status": doc.status,
		"updates": [
			{
				"posted_on": u.posted_on,
				"author_name": u.author_name,
				"from_portal": u.from_portal,
				"message": u.message,
				"photo": u.photo,
			}
			for u in doc.updates
		],
	}


@frappe.whitelist()
def post_maintenance_update(request, message=None, photo=None):
	"""Tenant posts a message/photo to their OWN request. Server-side scoped: the
	caller cannot target another tenant's request, and a photo URL must be a File the
	caller owns (they just uploaded it) — never an arbitrary private file reference."""
	from bunood_realestate.real_estate.doctype.maintenance_request.maintenance_request import append_update

	customers = _require_tenant()
	name = _owned_request(request, customers)

	if photo:
		owns_file = frappe.db.exists("File", {"file_url": photo, "owner": frappe.session.user})
		if not owns_file:
			frappe.throw(_("The attached photo could not be verified."), frappe.PermissionError)

	doc = frappe.get_doc("Maintenance Request", name)
	append_update(doc, message, photo, from_portal=1)
	return {"name": doc.name}
