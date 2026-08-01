# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""CAM (Common Area Maintenance) / service-charge apportionment — a thin PERIOD MATERIALIZER
over the verified Charge Engine, NOT a second billing engine.

A property defines shared per-period pools (cleaning, security, elevator…) as `Property Service
Charge` lines. Each period this module reads LIVE occupancy/area, splits the pool across the
billable units (the property stays the single source of truth — no per-lease copies to drift),
and inserts `Charge Schedule` rows (is_cam=1) that the EXISTING generate_due_charge_invoices posts
as native, tax-homogeneous, dimension-tagged, idempotent Sales Invoices. CAM never uses a rent
Service Item, so the cash-basis owner payout excludes it by construction (management.py filters
rent POSITIVELY by item_code=rent_item).
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

from bunood_realestate.real_estate.apportion import split_amount
from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import (
	INSTALLMENTS_PER_YEAR,
	build_periods,
)


# --------------------------------------------------------------------------------------
# Pure core (offline-testable: plain values in, dict out — no DB)
# --------------------------------------------------------------------------------------
def cam_billed_total(pool, w_total_all, w_billable, owner_absorbs):
	"""The portion of the period pool actually BILLED to tenants.
	Owner-absorbs: pool scaled by billable/total weight (vacant slice stays with the owner).
	Redistribute: the whole pool is billed across the occupied units.
	Either way, with NO occupied weight there is nobody to bill — the owner keeps the whole
	pool (a redistribute policy must never conjure a bill onto a fully-vacant property)."""
	pool = flt(pool)
	if flt(w_total_all) <= 0 or flt(w_billable) <= 0:
		return 0.0
	if owner_absorbs:
		return flt(pool * flt(w_billable) / flt(w_total_all), 2)
	return flt(pool, 2)


def apportion_cam(pool, units, owner_absorbs=True):
	"""PURE. ``units``: list of {unit, weight>=0, billable(bool), lease_contract, customer}.
	cam_exclude units are already OMITTED by the caller; vacant rentable units are present with
	billable=False so they sit in the denominator (owner absorbs their slice). Returns
	{shares, billed_total, owner_share}; the billed shares sum EXACTLY to billed_total."""
	billable = [u for u in units if u.get("billable") and flt(u.get("weight")) > 0]
	w_total_all = sum(flt(u.get("weight")) for u in units)
	w_billable = sum(flt(u.get("weight")) for u in billable)
	target_billed = cam_billed_total(pool, w_total_all, w_billable, owner_absorbs)
	shares = split_amount(target_billed, [flt(u["weight"]) for u in billable])
	out = [
		{
			"unit": u["unit"], "lease_contract": u.get("lease_contract"),
			"customer": u.get("customer"), "weight": flt(u["weight"]), "share": s,
		}
		for u, s in zip(billable, shares)
	]
	# owner_share is what is left AFTER the shares actually charged — derived from the real
	# billed lines, never the theoretical target, so pool == billed + owner_share always holds
	# (e.g. all-vacant redistribute: no shares → owner keeps the whole pool, nothing vanishes).
	billed = flt(sum(x["share"] for x in out), 2)
	return {
		"shares": out,
		"billed_total": billed,
		"owner_share": flt(flt(pool, 2) - billed, 2),
	}


def _unit_weight(basis, unit_row):
	"""Basis → weight (kept out of the pure fn; the materializer resolves live values).
	Weights are clamped to >= 0: a negative area / cam_weight would push the billable ratio
	above 1 and over-recover the pool, so a bad input contributes nothing rather than harm."""
	if basis == "Equal":
		return 1.0
	if basis == "Custom Weight":
		return max(0.0, flt(unit_row.get("cam_weight") or 0))
	if basis == "Rent Share":
		return max(0.0, flt(unit_row.get("annual_rent") or 0))
	return max(0.0, flt(unit_row.get("area_sqm") or 0))  # Area (default)


# --------------------------------------------------------------------------------------
# Materializer (DB) — build Planned Charge Schedule rows the existing generator bills
# --------------------------------------------------------------------------------------
def generate_cam_schedule(property=None):
	"""Scheduler entrypoint (daily, BEFORE generate_due_charge_invoices).

	A CAM period is materialized ATOMICALLY and EXACTLY ONCE, the day its due date arrives —
	never pre-materialized with a lead window. This is deliberate: each period's shares are
	struck from the occupancy read AT the due date, so a period is never re-opened by a later
	lease change (which, with immutable already-invoiced rows, would over- or under-recover the
	pool — especially under Redistribute). Per-property transaction under a row lock, fail-loud."""
	settings = frappe.get_single("Real Estate Settings")
	default_vacant = settings.get("cam_default_vacant_policy") or "Owner Absorbs"
	# CAM reads occupancy at the due date, so we never look past today (no lead_days).
	cutoff = nowdate()

	props = [property] if property else frappe.get_all(
		"Property", filters={"docstatus": ["<", 2]}, pluck="name"
	)
	created = 0
	for prop_name in props:
		try:
			created += _materialize_property(prop_name, cutoff, default_vacant)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: CAM materialization failed",
				message=f"Property {prop_name}\n\n{frappe.get_traceback()}",
			)
	return created


def _materialize_property(prop_name, cutoff, default_vacant):
	from bunood_realestate.real_estate.charge_engine import charge_due_date

	# Serialize concurrent materializations of the SAME property (daily job vs manual button,
	# or two rapid button clicks): the loser blocks here and, once the winner commits, sees the
	# winner's rows via the period-level idempotency guard — no duplicate CAM lines.
	frappe.db.get_value("Property", prop_name, "name", for_update=True)
	prop = frappe.get_doc("Property", prop_name)
	lines = [l for l in (prop.get("service_charges") or []) if l.get("is_active") and l.get("charge_type")]
	if not lines:
		return 0
	units = _property_units(prop_name)  # all non-excluded rentable units (with area/weight)
	created = 0
	for line in lines:
		cycle = line.billing_cycle or "Monthly"
		annual_equiv = flt(line.pool_amount) * INSTALLMENTS_PER_YEAR[cycle]
		start = getdate(line.charge_start_date or nowdate())
		end = getdate(line.charge_end_date) if line.charge_end_date else add_days(getdate(cutoff), 400)
		if start > end:
			continue
		timing = line.billing_timing or "Arrears"
		category = frappe.db.get_value("Charge Type", line.charge_type, "charge_kind")
		for p in build_periods(start, end, cycle, annual_equiv):
			due = charge_due_date(p["period_start"], p["period_end"], timing)
			if getdate(due) > getdate(cutoff):
				continue
			created += _materialize_period(prop, line, p, due, cycle, category, units, default_vacant)
	return created


def _materialize_period(prop, line, p, due, cycle, category, units, default_vacant):
	# PERIOD-LEVEL idempotency: a period is all-or-nothing. If ANY CAM row already exists for
	# (property, charge_type, period_start) — in any state, including Cancelled/Failed — the
	# period was already struck; never re-open it (that is what would let a later occupancy
	# change over-recover the pool under Redistribute). Combined with the property row lock,
	# this also closes the check-then-insert duplicate race.
	if frappe.db.exists(
		"Charge Schedule",
		{"property": prop.name, "charge_type": line.charge_type,
		 "period_start": p["period_start"], "is_cam": 1},
	):
		return 0
	# Occupancy is evaluated ONCE, at the period DUE DATE, for the whole period (deterministic).
	# The pool for THIS period is p["base_amount"] — equal to line.pool_amount for a full
	# period, and PRORATED by build_periods for a short final period (so a stub period never
	# bills a full month's pool over fewer days).
	period_pool = flt(p["base_amount"])
	rows = []
	for u in units:
		lease = _active_lease_on(u, due)
		rows.append({
			"unit": u["unit"], "weight": _unit_weight(line.allocation_basis, u),
			"billable": bool(lease), "lease_contract": lease.get("name") if lease else None,
			"customer": lease.get("customer") if lease else None,
		})
	# A pool with occupied units but ZERO total weight (e.g. Area basis where no unit has an
	# area entered) would silently bill nobody. Surface it instead of vanishing the pool.
	if any(r["billable"] for r in rows) and sum(flt(r["weight"]) for r in rows) <= 0:
		frappe.log_error(
			title="Bunood: CAM line has zero total weight",
			message=(f"Property {prop.name} / Charge Type {line.charge_type} / period "
				f"{p['period_start']}: basis {line.allocation_basis} yields all-zero weights "
				f"— no CAM billed. Check unit area / CAM weight."),
		)
		return 0
	policy = line.vacant_policy or default_vacant
	result = apportion_cam(period_pool, rows, owner_absorbs=(policy != "Redistribute to Occupied"))
	created = 0
	for sh in result["shares"]:
		tax_template = _resolve_cam_tax(line, sh["lease_contract"])
		frappe.get_doc({
			"doctype": "Charge Schedule",
			"lease_contract": sh["lease_contract"],
			"lease_charge_row": line.name,  # dual-meaning: the PSC child-row name for CAM rows
			"charge_type": line.charge_type,
			"category": category,
			"billing_method": "Fixed",
			"customer": sh["customer"],
			"property": prop.name,
			"unit": sh["unit"],
			"company": prop.company,
			"period_no": p["period_no"],
			"period_start": p["period_start"],
			"period_end": p["period_end"],
			"due_date": due,
			"billing_cycle": cycle,
			"base_amount": sh["share"],
			"is_prorated": p["is_prorated"],
			"revenue_account": line.revenue_account,
			"tax_template": tax_template,
			"status": "Planned",
			"is_cam": 1,
			"cam_pool": period_pool,
		}).insert(ignore_permissions=True)
		created += 1
	return created


def _property_units(prop_name):
	"""Rentable, non-excluded units of the property, with the fields the bases need."""
	return frappe.get_all(
		"Real Estate Unit",
		filters={"property": prop_name, "cam_exclude": 0},
		fields=["name as unit", "area_sqm", "cam_weight"],
	)


def _active_lease_on(unit_row, on_date):
	"""The lease Active on ``on_date`` covering this unit (else None → vacant). Reads the
	lease via the unit's Lease Unit rows, not the mutable current_lease flag."""
	# The `units` list is reused across every period in a run and this row is mutated in place
	# for the Rent-Share basis; reset the carried rent FIRST so a unit occupied in an earlier
	# period but vacant now cannot leave a stale weight in a later period's denominator.
	unit_row["annual_rent"] = 0.0
	rows = frappe.db.sql(
		"""
		SELECT lc.name, lc.customer,
		       (SELECT SUM(lu2.annual_rent) FROM `tabLease Unit` lu2 WHERE lu2.parent = lc.name AND lu2.unit = %(unit)s) AS annual_rent
		FROM `tabLease Contract` lc
		JOIN `tabLease Unit` lu ON lu.parent = lc.name
		WHERE lu.unit = %(unit)s AND lc.docstatus = 1 AND lc.status = 'Active'
		  AND lc.start_date <= %(on)s AND lc.end_date >= %(on)s
		ORDER BY lc.start_date DESC LIMIT 1
		""",
		{"unit": unit_row["unit"], "on": getdate(on_date)},
		as_dict=True,
	)
	if not rows:
		return None
	r = rows[0]
	unit_row["annual_rent"] = flt(r.annual_rent)  # feed Rent-Share basis
	return r


def _resolve_cam_tax(line, lease_contract):
	if line.tax_template:
		return line.tax_template
	from bunood_realestate.real_estate.company_settings import get_company_config

	ct = frappe.db.get_value("Lease Contract", lease_contract, "contract_type") if lease_contract else None
	cfg = get_company_config(frappe.db.get_value("Property", line.parent, "company")) or frappe._dict()
	return cfg.commercial_tax_template if ct == "Commercial" else cfg.residential_tax_template


def resync_cam_line(property):
	"""Property.on_update: a CAM definition change discards ONLY still-Planned, un-invoiced CAM
	rows (a discardable cache of the current period) so they re-materialize next run with the
	fresh pool + occupancy. Invoiced periods are immutable."""
	rows = frappe.get_all(
		"Charge Schedule",
		filters={"property": property, "is_cam": 1, "status": "Planned", "sales_invoice": ["in", [None, ""]]},
		pluck="name",
	)
	deleted = 0
	for name in rows:
		# Re-check UNDER a row lock: the charge generator may have flipped this row
		# Planned -> Invoiced (and stamped a sales_invoice) between the read above and here.
		# A plain force-delete would then destroy a row backing a submitted invoice, orphaning
		# it and letting the period re-materialize + re-bill. Skip anything no longer Planned.
		guard = frappe.db.get_value(
			"Charge Schedule", name, ["status", "sales_invoice"], for_update=True, as_dict=True
		)
		if not guard or guard.status != "Planned" or guard.sales_invoice:
			continue
		frappe.delete_doc("Charge Schedule", name, ignore_permissions=True, force=True)
		deleted += 1
	return deleted


@frappe.whitelist()
def generate_cam_now(property=None):
	"""Manual trigger (button). Materializes due CAM periods; the charge generator then bills them."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return generate_cam_schedule(property=property)
