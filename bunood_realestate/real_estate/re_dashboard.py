# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Data for the Real Estate dashboard page (KPI cards + rent chart + overdue list).
All figures are scoped to the companies the caller is permitted to see."""

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, nowdate


def _allowed_companies():
	return frappe.get_list("Company", pluck="name") or []


@frappe.whitelist()
def dashboard_data():
	companies = _allowed_companies()
	if not companies:
		return {"kpis": [], "chart": {"labels": [], "values": []}, "overdue": []}
	comp = tuple(companies) if len(companies) > 1 else (companies[0], companies[0])

	properties = frappe.db.count("Property", {"company": ["in", companies], "status": "Active"})
	active_leases = frappe.db.count(
		"Lease Contract", {"company": ["in", companies], "status": "Active", "docstatus": 1}
	)

	# Occupancy from the AUTHORITATIVE source — units actually held by a submitted Active
	# lease — not the mutable Real Estate Unit.status flag (which an operator can edit and
	# which can lag behind cancels/terminations). This can never diverge from real leases.
	units_total = flt(
		frappe.db.sql(
			"SELECT COUNT(*) FROM `tabReal Estate Unit` reu JOIN `tabProperty` p ON p.name = reu.property "
			"WHERE p.company IN %(comp)s",
			{"comp": comp},
		)[0][0]
	)
	occupied = flt(
		frappe.db.sql(
			"SELECT COUNT(DISTINCT lu.unit) FROM `tabLease Contract` lc "
			"JOIN `tabLease Unit` lu ON lu.parent = lc.name "
			"WHERE lc.docstatus = 1 AND lc.status = 'Active' AND lc.company IN %(comp)s",
			{"comp": comp},
		)[0][0]
	)
	occupancy_pct = round(occupied * 100.0 / units_total, 1) if units_total else 0.0

	overdue_amount = flt(
		frappe.db.sql(
			"""
			SELECT COALESCE(SUM(si.outstanding_amount), 0)
			FROM `tabRent Schedule` rs
			JOIN `tabSales Invoice` si ON si.name = rs.sales_invoice
			WHERE rs.company IN %(comp)s AND si.docstatus = 1
			  AND si.outstanding_amount > 0 AND rs.due_date < CURDATE()
			""",
			{"comp": comp},
		)[0][0]
	)

	kpis = [
		{"key": "properties", "label": _("Properties"), "value": properties, "tone": "green", "icon": "building"},
		{"key": "occupancy", "label": _("Occupancy %"), "value": f"{occupancy_pct}%", "tone": "gold", "icon": "pie-chart"},
		{"key": "active_leases", "label": _("Active Leases"), "value": active_leases, "tone": "blue", "icon": "file-text"},
		{"key": "vacant", "label": _("Vacant Units"), "value": units_total - occupied, "tone": "sky", "icon": "home"},
		{"key": "overdue", "label": _("Overdue"), "value": frappe.utils.fmt_money(overdue_amount), "tone": "amber", "icon": "alert-triangle"},
	]

	return {
		"kpis": kpis,
		"chart": _monthly_rent_chart(comp),
		"overdue": _top_overdue(comp),
	}


def _monthly_rent_chart(comp):
	"""Rent scheduled per month for the last 12 months (by due date)."""
	start = getdate(add_months(nowdate(), -11)).replace(day=1)
	rows = frappe.db.sql(
		"""
		SELECT DATE_FORMAT(rs.due_date, '%%Y-%%m') AS ym, COALESCE(SUM(rs.base_amount), 0) AS total
		FROM `tabRent Schedule` rs
		WHERE rs.company IN %(comp)s AND rs.due_date >= %(start)s
		GROUP BY ym ORDER BY ym
		""",
		{"comp": comp, "start": start},
		as_dict=True,
	)
	by_month = {r.ym: flt(r.total) for r in rows}
	labels, values = [], []
	cur = start
	for _i in range(12):
		ym = cur.strftime("%Y-%m")
		labels.append(cur.strftime("%b %Y"))
		values.append(by_month.get(ym, 0.0))
		cur = getdate(add_months(cur, 1))
	return {"labels": labels, "values": values}


def _top_overdue(comp):
	rows = frappe.db.sql(
		"""
		SELECT si.customer AS customer, rs.property AS property,
		       SUM(si.outstanding_amount) AS amount
		FROM `tabRent Schedule` rs
		JOIN `tabSales Invoice` si ON si.name = rs.sales_invoice
		WHERE rs.company IN %(comp)s AND si.docstatus = 1
		  AND si.outstanding_amount > 0 AND rs.due_date < CURDATE()
		GROUP BY si.customer, rs.property
		ORDER BY amount DESC LIMIT 6
		""",
		{"comp": comp},
		as_dict=True,
	)
	for r in rows:
		r["amount_fmt"] = frappe.utils.fmt_money(flt(r.amount))
	return rows
