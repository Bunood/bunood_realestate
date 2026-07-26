# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Expiring Leases — the desk work-list for renewals: which Active leases end within the
window, how many days are left, and whether a renewal draft already exists. Company-scoped
to the caller's permitted companies. Read-only; complements (not duplicates) the renewal
notifications (transient alerts) and upcoming_renewals API (button feed)."""

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, nowdate


def execute(filters=None):
	filters = filters or {}
	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), []

	days = int(filters.get("days") or 60)
	today = nowdate()
	conditions = ["lc.status = 'Active'", "lc.docstatus = 1", "lc.end_date BETWEEN %(today)s AND %(until)s"]
	values = {"today": today, "until": add_days(today, days)}

	company = filters.get("company")
	if company:
		if company not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("lc.company = %(company)s")
		values["company"] = company
	else:
		conditions.append("lc.company IN %(allowed)s")
		values["allowed"] = tuple(allowed) if len(allowed) > 1 else (allowed[0], allowed[0])

	if filters.get("auto_renew_only"):
		conditions.append("lc.auto_renew = 1")

	rows = frappe.db.sql(
		f"""
		SELECT lc.name AS lease, lc.property, lc.customer, lc.end_date,
		       lc.annual_rent_total, lc.auto_renew
		FROM `tabLease Contract` lc
		WHERE {" AND ".join(conditions)}
		ORDER BY lc.end_date ASC
		""",
		values,
		as_dict=True,
	)

	# One query for renewal existence (no per-row lookup).
	names = [r.lease for r in rows]
	drafted = set()
	if names:
		for d in frappe.get_all("Lease Contract", filters={"parent_lease": ["in", names]}, fields=["parent_lease"]):
			drafted.add(d.parent_lease)

	for r in rows:
		r["days_left"] = date_diff(r.end_date, today)
		r["renewal_drafted"] = 1 if r.lease in drafted else 0
		r["tenant_name"] = frappe.db.get_value("Customer", r.customer, "customer_name") or r.customer

	return _columns(), rows


def _columns():
	return [
		{"label": _("Lease"), "fieldname": "lease", "fieldtype": "Link", "options": "Lease Contract", "width": 150},
		{"label": _("Property"), "fieldname": "property", "fieldtype": "Link", "options": "Property", "width": 160},
		{"label": _("Tenant"), "fieldname": "tenant_name", "fieldtype": "Data", "width": 160},
		{"label": _("End Date"), "fieldname": "end_date", "fieldtype": "Date", "width": 100},
		{"label": _("Days Left"), "fieldname": "days_left", "fieldtype": "Int", "width": 90},
		{"label": _("Annual Rent"), "fieldname": "annual_rent_total", "fieldtype": "Currency", "width": 120},
		{"label": _("Auto Renew"), "fieldname": "auto_renew", "fieldtype": "Check", "width": 90},
		{"label": _("Renewal Drafted"), "fieldname": "renewal_drafted", "fieldtype": "Check", "width": 120},
	]
