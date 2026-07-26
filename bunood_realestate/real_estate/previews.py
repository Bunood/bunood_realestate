# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Read-only 'preview the data before you print' endpoints.

Print formats live in a dedicated system-wide module; until then (and afterwards, as a
quick on-screen check) these return the live figures a desk button renders in a summary
dialog. Every endpoint re-checks record permission — a user restricted by a
Company/Property User Permission sees only their own leases/properties.
"""

import frappe
from frappe.utils import flt


@frappe.whitelist()
def lease_preview(lease_contract):
	"""Live summary for one lease: parties, term, rent, deposit held, dues, schedule
	progress — the data you'd want to eyeball before printing the contract."""
	lease = frappe.get_doc("Lease Contract", lease_contract)
	lease.check_permission("read")

	from bunood_realestate.real_estate.collections import _tenant_outstanding

	rows = frappe.get_all("Rent Schedule", filters={"lease_contract": lease_contract}, fields=["status"])
	schedule = {}
	for r in rows:
		schedule[r.status] = schedule.get(r.status, 0) + 1

	return {
		"name": lease.name,
		"tenant": lease.customer,
		"tenant_name": frappe.db.get_value("Customer", lease.customer, "customer_name") or lease.customer,
		"property": lease.property,
		"company": lease.company,
		"currency": frappe.get_cached_value("Company", lease.company, "default_currency") or "SAR",
		"start_date": lease.start_date,
		"end_date": lease.end_date,
		"hijri_start_date": lease.get("hijri_start_date"),
		"hijri_end_date": lease.get("hijri_end_date"),
		"status": lease.status,
		"annual_rent_total": flt(lease.annual_rent_total),
		"deposit_amount": flt(lease.get("deposit_amount")),
		"deposit_held": flt(lease.get("deposit_received")) - flt(lease.get("deposit_refunded")),
		"outstanding": _tenant_outstanding(lease.customer, lease.company),
		"units": [
			{"unit": u.get("unit"), "annual_rent": flt(u.get("annual_rent"))}
			for u in (lease.get("units") or [])
		],
		"schedule": schedule,
	}


@frappe.whitelist()
def property_preview(property):
	"""Live summary for one property: units, occupancy, active leases — before printing."""
	p = frappe.get_doc("Property", property)
	p.check_permission("read")

	units = frappe.get_all(
		"Real Estate Unit", filters={"property": property}, fields=["name", "status"]
	)
	occupied = flt(
		frappe.db.sql(
			"""
			SELECT COUNT(DISTINCT lu.unit)
			FROM `tabLease Contract` lc
			JOIN `tabLease Unit` lu ON lu.parent = lc.name
			JOIN `tabReal Estate Unit` reu ON reu.name = lu.unit
			WHERE lc.docstatus = 1 AND lc.status = 'Active' AND reu.property = %s
			""",
			property,
		)[0][0]
	)
	total = len(units)
	return {
		"name": p.name,
		"property_name": p.get("property_name") or p.name,
		"company": p.company,
		"units_total": total,
		"occupied": int(occupied),
		"vacant": int(max(0, total - occupied)),
		"occupancy_pct": round(occupied * 100.0 / total, 1) if total else 0.0,
		"active_leases": frappe.db.count(
			"Lease Contract", {"property": property, "status": "Active", "docstatus": 1}
		),
	}
