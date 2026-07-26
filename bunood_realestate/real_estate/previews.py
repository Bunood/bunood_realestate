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
def property_building(property):
	"""Units of a property grouped by floor, each with its live status/tenant/rent — the
	data behind the visual 'building' view + units board. Occupancy is derived from
	submitted Active leases (authoritative), not the mutable unit.status flag."""
	p = frappe.get_doc("Property", property)
	p.check_permission("read")

	units = frappe.get_all(
		"Real Estate Unit",
		filters={"property": property},
		fields=[
			"name", "unit_number", "unit_type", "floor", "area_sqm",
			"rooms_count", "bathrooms", "market_rent", "status", "view_type",
		],
		order_by="floor asc, unit_number asc",
	)

	# unit -> live tenancy from the active lease (authoritative).
	leased = {}
	for r in frappe.db.sql(
		"""
		SELECT lu.unit AS unit, lc.name AS lease, lc.customer AS customer,
		       cust.customer_name AS tenant_name, lu.annual_rent AS rent
		FROM `tabLease Unit` lu
		JOIN `tabLease Contract` lc ON lc.name = lu.parent
		JOIN `tabReal Estate Unit` reu ON reu.name = lu.unit
		LEFT JOIN `tabCustomer` cust ON cust.name = lc.customer
		WHERE lc.docstatus = 1 AND lc.status = 'Active' AND reu.property = %s
		""",
		property,
		as_dict=True,
	):
		leased.setdefault(r.unit, r)

	floors = {}
	counts = {"Occupied": 0, "Reserved": 0, "Vacant": 0, "Maintenance": 0}
	for u in units:
		live = leased.get(u.name)
		if live:
			state = "Occupied"
			tenant, tenant_name, rent, lease = live.customer, live.tenant_name, flt(live.rent), live.lease
		else:
			# No active lease → fall back to the unit's own flag, but never claim "Occupied".
			state = u.status if u.status in ("Reserved", "Maintenance") else "Vacant"
			tenant = tenant_name = lease = None
			rent = flt(u.market_rent)
		counts[state] = counts.get(state, 0) + 1

		key = u.floor if u.floor is not None else 0
		floors.setdefault(key, []).append({
			"name": u.name,
			"unit_number": u.unit_number or u.name,
			"unit_type": u.unit_type,
			"floor": u.floor,
			"area_sqm": flt(u.area_sqm),
			"rooms_count": u.rooms_count,
			"bathrooms": u.bathrooms,
			"view_type": u.view_type,
			"state": state,
			"tenant": tenant,
			"tenant_name": tenant_name,
			"rent": rent,
			"lease": lease,
		})

	total = len(units)
	floor_list = [
		{"floor": k, "units": floors[k]}
		for k in sorted(floors.keys(), reverse=True)  # top floor first (building view)
	]
	return {
		"property": p.name,
		"property_name": p.get("property_name") or p.name,
		"company": p.company,
		"currency": frappe.get_cached_value("Company", p.company, "default_currency") or "SAR",
		"floors": floor_list,
		"totals": {
			"total": total,
			"occupied": counts["Occupied"],
			"reserved": counts["Reserved"],
			"vacant": counts["Vacant"],
			"maintenance": counts["Maintenance"],
			"occupancy_pct": round(counts["Occupied"] * 100.0 / total, 1) if total else 0.0,
		},
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
