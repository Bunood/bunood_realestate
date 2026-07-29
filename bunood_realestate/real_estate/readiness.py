# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Unit readiness — a COMPUTED operational indicator (never stored, so it can never drift;
the unit.status lesson applied). Tells the operations team exactly what a unit still needs
before it can be offered for rent: photos, meters, inventory, pricing."""

import os

import frappe
from frappe import _
from frappe.utils import flt

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".svg"}


def _has_photos(unit):
	"""True only when an actual IMAGE is attached to the unit — a deed PDF must not
	satisfy the 'Photos attached' readiness check."""
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Real Estate Unit", "attached_to_name": unit, "is_folder": 0},
		fields=["file_name", "file_url"],
		limit=50,
	)
	for f in files:
		name = f.file_name or f.file_url or ""
		if os.path.splitext(name)[1].lower() in _IMAGE_EXTS:
			return True
	return False

# (key, label) in display order — equal weights. A signal is a bool.
READINESS_CHECKS = [
	("pricing", "Market rent set"),
	("meters", "Utility meters recorded"),
	("inventory", "Furniture & fixtures registered"),
	("photos", "Photos attached"),
]


def compute_readiness(signals):
	"""Pure & testable: {key: bool} → {pct, missing:[{key,label}], checks:[{key,label,ok}]}."""
	checks = []
	done = 0
	for key, label in READINESS_CHECKS:
		ok = bool(signals.get(key))
		checks.append({"key": key, "label": label, "ok": ok})
		if ok:
			done += 1
	total = len(READINESS_CHECKS) or 1
	return {
		"pct": int(round(100.0 * done / total)),
		"checks": checks,
		"missing": [c for c in checks if not c["ok"]],
	}


@frappe.whitelist()
def unit_readiness(unit):
	"""Live readiness for one unit. Read-only; permission enforced on the unit itself."""
	doc = frappe.get_doc("Real Estate Unit", unit)
	doc.check_permission("read")
	signals = {
		"pricing": flt(doc.get("market_rent")) > 0,
		"meters": bool(doc.get("electricity_meter") or doc.get("water_meter")),
		"inventory": bool(frappe.db.exists("Unit Inventory Item", {"unit": unit})),
		"photos": _has_photos(unit),
	}
	out = compute_readiness(signals)
	out["unit"] = unit
	return out
