# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Portal helpers. Every query is scoped to the party (Customer for tenants, Supplier
for owners) linked to the logged-in portal user via Contact → Dynamic Link — a tenant
sees only their OWN leases/invoices/dues, an owner only their OWN properties/payouts.
Guests get nothing."""

import frappe
from frappe import _


def _linked_parties(link_doctype, user=None):
	"""The record(s) of `link_doctype` the logged-in portal user is linked to (via their
	Contact → Dynamic Link). Returns [] for Guest or an unlinked user — callers MUST
	treat [] as 'no access'. One implementation for every party type (no duplication)."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return []
	contacts = frappe.get_all("Contact", filters={"user": user}, pluck="name")
	if not contacts:
		return []
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": ["in", contacts], "link_doctype": link_doctype},
		pluck="link_name",
	)
	return sorted(set(filter(None, links)))


def customers_for_user(user=None):
	"""The Customer(s) the portal user is linked to (tenant scope)."""
	return _linked_parties("Customer", user)


def suppliers_for_user(user=None):
	"""The Supplier(s) the portal user is linked to (owner scope)."""
	return _linked_parties("Supplier", user)


def _require_tenant():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in to access the tenant portal."), frappe.PermissionError)
	customers = customers_for_user()
	if not customers:
		frappe.throw(_("Your account is not linked to a tenant."), frappe.PermissionError)
	return customers


def _require_supplier_link(role_label):
	"""Shared gate for every Supplier-scoped portal (owner, contractor): the user must be
	logged in AND linked to at least one Supplier. Returns that Supplier list (never []).
	One implementation — no per-role duplication."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in to access the portal."), frappe.PermissionError)
	suppliers = suppliers_for_user()
	if not suppliers:
		frappe.throw(_("Your account is not linked to a {0}.").format(role_label), frappe.PermissionError)
	return suppliers


def _require_owner():
	return _require_supplier_link(_("owner"))


def _require_vendor():
	return _require_supplier_link(_("contractor"))


@frappe.whitelist()
def owner_properties():
	"""The logged-in owner's properties (scoped to their linked Supplier[s])."""
	suppliers = _require_owner()
	return frappe.get_all(
		"Property",
		filters={"owner_party": ["in", suppliers]},
		fields=["name", "property_name", "company", "management_behavior", "management_fee_percentage"],
		order_by="property_name asc",
	)


@frappe.whitelist()
def owner_payouts():
	"""The logged-in owner's posted payouts (scoped to their linked Supplier[s])."""
	suppliers = _require_owner()
	return frappe.get_all(
		"Owner Payout",
		filters={"owner_party": ["in", suppliers], "status": "Posted"},
		fields=["name", "property", "from_date", "to_date", "rent_base", "fee_amount", "owner_payout", "journal_entry"],
		order_by="from_date desc",
		limit=100,
	)


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
	# Coerce to a plain string so a crafted filter-dict can never turn get_value into
	# an arbitrary lookup — it must be a document name.
	request = frappe.utils.cstr(request or "")
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
		# Must be a server file path (never a javascript:/data:/http: scheme that would
		# execute if rendered into an href) AND a File the caller owns (just uploaded).
		if not photo.startswith("/"):
			frappe.throw(_("Invalid photo reference."), frappe.PermissionError)
		owns_file = frappe.db.exists("File", {"file_url": photo, "owner": frappe.session.user})
		if not owns_file:
			frappe.throw(_("The attached photo could not be verified."), frappe.PermissionError)

	doc = frappe.get_doc("Maintenance Request", name)
	append_update(doc, message, photo, from_portal=1)
	return {"name": doc.name}


# ---------------------------------------------------------------------------
# Contractor (vendor) portal — assigned maintenance work orders
# ---------------------------------------------------------------------------

_VENDOR_STATUSES = ("Open", "In Progress", "Done")  # a vendor can progress, never Cancel


@frappe.whitelist()
def vendor_work_orders():
	"""Maintenance work orders assigned to the logged-in contractor (their linked
	Supplier[s]). A contractor sees ONLY their own jobs."""
	suppliers = _require_vendor()
	return frappe.get_all(
		"Maintenance Work Order",
		filters={"contractor": ["in", suppliers]},
		fields=["name", "maintenance_request", "property", "unit", "status", "scheduled_date", "notes", "total_cost"],
		order_by="scheduled_date desc",
		limit=100,
	)


@frappe.whitelist()
def update_work_order(work_order, status=None, notes=None):
	"""A contractor updates the status/notes of their OWN work order. Server-side scoped
	to the caller's Supplier(s); only status + notes may change, and only to a non-Cancel
	status — the caller cannot touch cost, assignment, or another vendor's job."""
	suppliers = _require_vendor()
	wo = frappe.db.get_value(
		"Maintenance Work Order", frappe.utils.cstr(work_order or ""), ["name", "contractor"], as_dict=True
	)
	if not wo:
		frappe.throw(_("Work order not found."), frappe.DoesNotExistError)
	if wo.contractor not in suppliers:
		frappe.throw(_("You do not have access to this work order."), frappe.PermissionError)

	doc = frappe.get_doc("Maintenance Work Order", wo.name)
	if status:
		if status not in _VENDOR_STATUSES:
			frappe.throw(_("Invalid status."))
		doc.status = status
	if notes is not None:
		doc.notes = (notes or "").strip()[:2000]
	doc.flags.ignore_permissions = True  # portal user has no desk perms; scoped above
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}
