# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Shared GL posting helpers.

A Cost Center in ERPNext belongs to exactly one Company, and every Profit-and-Loss
Journal Entry line REQUIRES one. Real Estate Settings holds a single global default
cost center, but our money documents are per-company (company is fetched from the
property / land). Blindly stamping the settings default onto a JE for a *different*
company makes ERPNext reject the whole submit ("Cost Center X does not belong to
Company Y"), and stamping nothing on a P&L line is rejected too. So always resolve a
cost center that actually belongs to the voucher's own company.
"""

import frappe
from frappe import _


def resolve_cost_center(company):
	"""Return a Cost Center that belongs to ``company``, or ``None``.

	Prefers the Real Estate Settings default, but ONLY when it belongs to this
	company; otherwise falls back to the company's own default cost center.
	"""
	if not company:
		return None
	settings_cc = frappe.db.get_single_value("Real Estate Settings", "default_cost_center")
	if settings_cc and frappe.db.get_value("Cost Center", settings_cc, "company") == company:
		return settings_cc
	return frappe.get_cached_value("Company", company, "cost_center") or None


def require_cost_center(company):
	"""Like :func:`resolve_cost_center` but raises a clear error when none resolves.

	Use for JE Profit-and-Loss lines, which ERPNext will not accept without a
	(company-matching) cost center — surfacing the fix up front beats a raw
	validation error mid-submit.
	"""
	cc = resolve_cost_center(company)
	if not cc:
		frappe.throw(
			_(
				"No Cost Center found for company {0}. Set a default Cost Center on the "
				"Company (or a matching one in Real Estate Settings) before posting."
			).format(company or "?")
		)
	return cc
