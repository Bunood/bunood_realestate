# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Charge Engine — Phase 1 seed + migration (idempotent; safe on fresh install AND upgrade).

1. Seed the three default Billing Policy rows and the common Utility Charge Types (so the
   engine and the migration below have their masters regardless of patch/fixture ordering).
2. Migrate each lease's dead scalar utility fields (electricity/water/gas/parking_annual)
   into `Lease Charge` rows — Fixed, Annual cycle (lossless: annual amount = one period),
   billed in Arrears. The old scalars are left in place (read-only history); the engine now
   reads the child rows. Re-running is safe: a lease already carrying a charge of that type
   is skipped."""

import frappe
from frappe.utils import flt

# scalar fieldname -> (Charge Type name, label)
_UTILITY_MAP = [
	("electricity_annual", "Electricity"),
	("water_annual", "Water"),
	("gas_annual", "Gas"),
	("parking_annual", "Parking"),
]

_BILLING_POLICIES = [
	("Separate Invoices", "separate", "One Sales Invoice per charge (default)."),
	("Group by Category", "group_by_category", "One Sales Invoice per Charge Type kind."),
	("Single Invoice", "single", "One Sales Invoice for all of the tenant's due charges."),
]


def _ensure_billing_policies():
	for name, behavior, desc in _BILLING_POLICIES:
		if not frappe.db.exists("Billing Policy", name):
			frappe.get_doc({
				"doctype": "Billing Policy", "policy_name": name,
				"behavior": behavior, "is_active": 1, "description": desc,
			}).insert(ignore_permissions=True)


def _ensure_utility_charge_types():
	for _scalar, ct_name in _UTILITY_MAP:
		if not frappe.db.exists("Charge Type", ct_name):
			frappe.get_doc({
				"doctype": "Charge Type", "charge_type_name": ct_name,
				"charge_kind": "Utility", "is_active": 1,
				"is_recurring": 1, "default_billing_method": "Fixed",
			}).insert(ignore_permissions=True)


def _migrate_leases():
	"""Convert legacy scalars → Lease Charge rows; returns the migrated lease names."""
	# Only leases that actually carry a nonzero legacy utility scalar.
	conds = " OR ".join(f"IFNULL(`{s}`, 0) > 0" for s, _ in _UTILITY_MAP)
	leases = frappe.db.sql(
		f"SELECT name FROM `tabLease Contract` WHERE {conds}", as_dict=True
	)
	migrated = []
	for row in leases:
		lease = frappe.get_doc("Lease Contract", row.name)
		for scalar, ct_name in _UTILITY_MAP:
			amount = flt(lease.get(scalar))
			if amount <= 0:
				continue
			# Idempotency: skip if this lease already has a charge of this type.
			if frappe.db.exists(
				"Lease Charge",
				{"parent": lease.name, "parenttype": "Lease Contract", "charge_type": ct_name},
			):
				continue
			frappe.get_doc({
				"doctype": "Lease Charge",
				"parent": lease.name, "parenttype": "Lease Contract", "parentfield": "charges",
				"charge_type": ct_name,
				"billing_method": "Fixed",
				"amount": amount,
				"billing_cycle": "Annual",
				"billing_timing": "Arrears",
				"is_active": 1,
			}).insert(ignore_permissions=True)
			if row.name not in migrated:
				migrated.append(row.name)
	return migrated


def _seed_schedules(migrated):
	"""Seed Charge Schedule rows for migrated ACTIVE leases — without this, the migrated
	charges exist but are never billed (the generator reads Charge Schedule, and
	seed_charges_for_lease otherwise runs only in Lease Contract.on_submit, which an
	already-submitted lease never fires again). cutoff=today: never back-bill utilities."""
	from frappe.utils import nowdate

	from bunood_realestate.real_estate.charge_engine import seed_charges_for_lease

	for name in migrated:
		lease = frappe.get_doc("Lease Contract", name)
		if lease.docstatus == 1 and lease.status == "Active":
			seed_charges_for_lease(lease, cutoff=nowdate())


def execute():
	# Guard: if the Charge Engine doctypes aren't synced yet, do nothing (a later run retries).
	if not frappe.db.table_exists("Lease Charge") or not frappe.db.table_exists("Billing Policy"):
		return
	_ensure_billing_policies()
	_ensure_utility_charge_types()
	migrated = _migrate_leases()
	if frappe.db.table_exists("Charge Schedule"):
		_seed_schedules(migrated)
	frappe.db.commit()
