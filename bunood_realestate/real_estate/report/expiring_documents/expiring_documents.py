# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Expiring Documents — the compliance work-list: which Legal Documents (CR, licenses, Iqama,
Civil Defense…) expire within the window, how many days are left, and who is responsible.
Company-scoped to the caller's permitted companies. Perpetual documents (deed, VAT) are
excluded by construction. Read-only; complements the transient document-expiry alerts."""

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, nowdate

from bunood_realestate.real_estate.notifications import document_status


def execute(filters=None):
	filters = filters or {}
	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), []

	days = int(filters.get("days") or 90)
	today = nowdate()
	conditions = ["ld.is_perpetual = 0", "ld.status = 'Active'", "ld.expiry_date IS NOT NULL"]
	values = {"until": add_days(today, days), "today": today}

	# Upper bound always applies; the lower bound (hide already-expired) is optional.
	conditions.append("ld.expiry_date <= %(until)s")
	if not filters.get("include_expired"):
		conditions.append("ld.expiry_date >= %(today)s")

	company = filters.get("company")
	if company:
		if company not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("ld.company = %(company)s")
		values["company"] = company
	else:
		conditions.append("ld.company IN %(allowed)s")
		values["allowed"] = tuple(allowed) if len(allowed) > 1 else (allowed[0], allowed[0])

	if filters.get("document_type"):
		conditions.append("ld.document_type = %(document_type)s")
		values["document_type"] = filters["document_type"]

	rows = frappe.db.sql(
		f"""
		SELECT ld.name AS document, ld.document_type, ld.link_doctype, ld.link_name,
		       ld.document_number, ld.issuer, ld.issue_date, ld.expiry_date,
		       ld.responsible_user, ld.attachment, ld.company
		FROM `tabLegal Document` ld
		WHERE {" AND ".join(conditions)}
		ORDER BY ld.expiry_date ASC
		""",
		values,
		as_dict=True,
	)

	labels = _resolve_entity_labels(rows)
	grace_by_type = _grace_by_type(rows)
	for r in rows:
		r["entity"] = labels.get((r.link_doctype, r.link_name), r.link_name)
		r["days_left"] = date_diff(r.expiry_date, today)
		r["has_attachment"] = 1 if r.attachment else 0
		r["expiry_status"] = document_status(
			r.expiry_date, today, is_perpetual=False, grace_days=grace_by_type.get(r.document_type, 0)
		)

	return _columns(), rows


def _grace_by_type(rows):
	"""One batched lookup of grace_days per distinct document type (never per-row) so an
	expired-but-still-in-grace document shows 'In Grace' rather than 'Expired'."""
	types = {r.document_type for r in rows if r.document_type}
	if not types:
		return {}
	return {
		d.name: int(d.grace_days or 0)
		for d in frappe.get_all(
			"RE Document Type", filters={"name": ["in", list(types)]}, fields=["name", "grace_days"]
		)
	}


def _resolve_entity_labels(rows):
	"""One batched lookup per entity doctype (max ~6), never per-row — resolve each linked
	entity's human title (Customer/Supplier name…) instead of its raw docname where possible."""
	by_dt = {}
	for r in rows:
		if r.link_doctype and r.link_name:
			by_dt.setdefault(r.link_doctype, set()).add(r.link_name)
	labels = {}
	for dt, names in by_dt.items():
		try:
			title_field = frappe.get_meta(dt).get_title_field() or "name"
		except Exception:
			title_field = "name"
		fields = ["name"] if title_field == "name" else ["name", title_field]
		for d in frappe.get_all(dt, filters={"name": ["in", list(names)]}, fields=fields):
			labels[(dt, d.name)] = d.get(title_field) or d.name
	return labels


def _columns():
	return [
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Link", "options": "Legal Document", "width": 130},
		{"label": _("Type"), "fieldname": "document_type", "fieldtype": "Link", "options": "RE Document Type", "width": 160},
		{"label": _("Entity Type"), "fieldname": "link_doctype", "fieldtype": "Data", "width": 110},
		{"label": _("Entity"), "fieldname": "entity", "fieldtype": "Data", "width": 170},
		{"label": _("Number"), "fieldname": "document_number", "fieldtype": "Data", "width": 130},
		{"label": _("Issuer"), "fieldname": "issuer", "fieldtype": "Data", "width": 130},
		{"label": _("Issue Date"), "fieldname": "issue_date", "fieldtype": "Date", "width": 100},
		{"label": _("Expiry Date"), "fieldname": "expiry_date", "fieldtype": "Date", "width": 100},
		{"label": _("Days Left"), "fieldname": "days_left", "fieldtype": "Int", "width": 90},
		{"label": _("Status"), "fieldname": "expiry_status", "fieldtype": "Data", "width": 90},
		{"label": _("Responsible"), "fieldname": "responsible_user", "fieldtype": "Link", "options": "User", "width": 140},
		{"label": _("Attach"), "fieldname": "has_attachment", "fieldtype": "Check", "width": 60},
	]
