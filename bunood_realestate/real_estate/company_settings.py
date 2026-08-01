# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Multi-company settings resolution — the SINGLE choke point for company-bound config.

One site can serve several companies. Company-bound accounts/items/templates live either on
the legacy `Real Estate Settings` Single (which remains the live config for ITS company —
zero-migration guarantee) or on a per-company `Real Estate Company Profile`. Every money
path resolves through here; nothing else may read PROFILE_FIELDS off the Single directly.

Resolution is WHOLE-BLOCK (no per-field cross-company inheritance — half a company's
accounts coming from another company is exactly the bug class this layer kills):

  1. an ENABLED profile for the company → its fields (+ GLOBAL_FIELDS from the Single);
  2. else, the Single when its `company` matches or is blank → the legacy branch that
     reproduces today's single-company behavior byte-for-byte, throw-for-throw;
  3. else None → ``require_company_config`` fails loud with an actionable message
     (the multi-company successor of the old "one site = one company" hard-throws).

GLOBAL_FIELDS (site-wide policy: auto-submit, lead days, late fees, notifications,
dimension enforcement) ALWAYS come from the Single, never from a profile.
"""

import frappe
from frappe import _

# Company-bound config — fieldnames are IDENTICAL on the Single and the Profile, so the
# resolved cfg is a drop-in replacement wherever the old `settings` object was used.
PROFILE_FIELDS = (
	"default_cost_center",
	"rent_income_account",
	"receivable_account",
	"tenant_deposit_account",
	"default_rent_item",
	"head_lease_item",
	"maintenance_item",
	"maintenance_expense_account",
	"owner_payout_expense_account",
	"opening_balance_account",
	"deduction_income_account",
	"commercial_tax_template",
	"residential_tax_template",
)

# Site-wide policy — stays on the Single by design (hard rule from the blueprint).
GLOBAL_FIELDS = (
	"invoice_issuance_policy",
	"invoice_lead_days",
	"due_soon_days",
	"enable_late_fees",
	"late_fee_charge_type",
	"late_fee_grace_days",
	"late_fee_type",
	"late_fee_value",
	"late_fee_cap",
	"enable_auto_notifications",
	"auto_draft_renewals",
	"enable_document_expiry_alerts",
	"dimension_enforcement",
	"require_mode_of_payment",
)

# Human labels for aggregated require_ messages (kept in one place, next to the registry).
FIELD_LABELS = {
	"default_cost_center": "Default Cost Center",
	"rent_income_account": "Rent Income Account",
	"receivable_account": "Receivable Account",
	"tenant_deposit_account": "Tenant Security Deposit Account",
	"default_rent_item": "Default Rent Item",
	"head_lease_item": "Head-Lease Item",
	"maintenance_item": "Maintenance Item",
	"maintenance_expense_account": "Maintenance Expense Account",
	"owner_payout_expense_account": "Owner Payout Expense Account",
	"opening_balance_account": "Opening Balance Account",
	"deduction_income_account": "Deposit Deduction Income Account",
	"commercial_tax_template": "Commercial Tax Template",
	"residential_tax_template": "Residential Tax Template",
}


# ------------------------------------------------------------------------------------
# Pure core (offline-testable: plain dicts in, dict/None out — no DB access)
# ------------------------------------------------------------------------------------
def resolve_config(company, profiles_by_company, single):
	"""Whole-block resolution. ``profiles_by_company``: {company: {field: value, "enabled": 0/1}};
	``single``: dict of the Real Estate Settings values. Returns a settings-shaped dict or None."""
	single = single or {}
	profile = (profiles_by_company or {}).get(company)
	if profile:
		if not profile.get("enabled", 1):
			# A DISABLED profile is TERMINAL for its company — "park a company" means its
			# documents fail loud instead of posting (the field's shipped contract). It
			# must never silently fall back to the Single's accounts: that would swap the
			# payout discriminators mid-stream with zero warning.
			return None
		cfg = {f: profile.get(f) for f in PROFILE_FIELDS}
		cfg.update({f: single.get(f) for f in GLOBAL_FIELDS})
		cfg["company"] = company
		cfg["_source"] = "profile"
		return frappe._dict(cfg)
	# Legacy branch: the Single serves ITS OWN company (or any company when it names none —
	# today's fresh-site behavior; such sites have no profiles at all).
	if not single.get("company") or single.get("company") == company:
		cfg = {f: single.get(f) for f in PROFILE_FIELDS + GLOBAL_FIELDS}
		cfg["company"] = company
		cfg["_source"] = "settings"
		return frappe._dict(cfg)
	return None


def missing_fields(cfg, fields):
	"""Pure: which of ``fields`` are blank on a resolved cfg."""
	return [f for f in (fields or []) if not (cfg or {}).get(f)]


def collect_values(fieldname, single, profiles):
	"""Pure: every configured value of ``fieldname`` across the Single + ALL profiles
	(enabled or not — historical config still guards discriminators site-wide)."""
	out = set()
	v = (single or {}).get(fieldname)
	if v:
		out.add(v)
	for p in (profiles or []):
		v = (p or {}).get(fieldname)
		if v:
			out.add(v)
	return out


# ------------------------------------------------------------------------------------
# DB wrappers (request-cached on frappe.local — never module-global)
# ------------------------------------------------------------------------------------
def _profiles_by_company():
	cache = getattr(frappe.local, "_bnd_company_profiles", None)
	if cache is None:
		rows = frappe.get_all(
			"Real Estate Company Profile",
			fields=["company", "enabled", *PROFILE_FIELDS],
		) if frappe.db.table_exists("Real Estate Company Profile") else []
		cache = {r["company"]: r for r in rows}
		frappe.local._bnd_company_profiles = cache
	return cache


def get_company_config(company, single=None):
	"""Resolved config for ``company`` or None. ``single`` may be passed to reuse an
	already-loaded Real Estate Settings doc (hot loops)."""
	if single is None:
		single = frappe.get_cached_doc("Real Estate Settings")
	single_dict = single.as_dict() if hasattr(single, "as_dict") else dict(single)
	return resolve_config(company, _profiles_by_company(), single_dict)


def require_company_config(company, fields=None, single=None):
	"""Resolve or fail LOUD — the multi-company successor of the old per-field checks and
	the "one site = one company" hard-throws. Aggregates EVERY missing field into one
	actionable message naming the source document."""
	cfg = get_company_config(company, single=single)
	if cfg is None:
		single_company = frappe.db.get_single_value("Real Estate Settings", "company")
		frappe.throw(
			_(
				"No Real Estate configuration for company {0}. Create a Real Estate Company "
				"Profile for it (Real Estate Settings applies only to {1})."
			).format(company, single_company or _("the default company"))
		)
	missing = missing_fields(cfg, fields)
	if missing:
		labels = ", ".join(_(FIELD_LABELS.get(f, f)) for f in missing)
		source = (
			_("Real Estate Company Profile {0}").format(company)
			if cfg.get("_source") == "profile"
			else _("Real Estate Settings")
		)
		frappe.throw(_("Set {0} on {1} before posting.").format(labels, source))
	return cfg


def all_configured_values(fieldname):
	"""Site-wide union of a config field over the Single + every profile (enabled or not).
	Used by cross-company discriminators (rent item / payout account) and the dimension guard."""
	single = frappe.get_cached_doc("Real Estate Settings")
	single_dict = single.as_dict() if hasattr(single, "as_dict") else dict(single)
	return collect_values(fieldname, single_dict, list(_profiles_by_company().values()))
