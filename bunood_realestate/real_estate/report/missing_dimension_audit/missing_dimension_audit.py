# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Missing Dimension Audit — the backfill gate for Phase 0 (plan-financial-reporting.md).

Lists every live GL Entry on a real-estate account (Real Estate Settings + Charge Type
income accounts) that carries NO Property dimension — i.e. money that is invisible to
every dimension-based report (Owner Ledger, Property P&L, statements). Fix these (amend
or reclassify), watch this report reach zero rows, then flip Dimension Enforcement from
Warn to Block. Company-scoped to the caller's permitted companies.
"""

import frappe
from frappe import _

from bunood_realestate.real_estate.dimension_guard import re_account_set


def execute(filters=None):
	filters = filters or {}
	settings = frappe.get_cached_doc("Real Estate Settings")
	accounts = re_account_set(settings)
	if not accounts:
		frappe.msgprint(_("No real-estate accounts are configured in Real Estate Settings yet."))
		return _columns(), []

	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), []

	conditions = [
		"gle.is_cancelled = 0",
		"gle.account IN %(accounts)s",
		"COALESCE(gle.property, '') = ''",
	]
	values = {"accounts": tuple(accounts) if len(accounts) > 1 else (next(iter(accounts)),) * 2}

	company = filters.get("company")
	if company:
		if company not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("gle.company = %(company)s")
		values["company"] = company
	else:
		conditions.append("gle.company IN %(allowed)s")
		values["allowed"] = tuple(allowed) if len(allowed) > 1 else (allowed[0], allowed[0])

	if filters.get("from_date"):
		conditions.append("gle.posting_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("gle.posting_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]
	if filters.get("account"):
		conditions.append("gle.account = %(account)s")
		values["account"] = filters["account"]

	rows = frappe.db.sql(
		f"""
		SELECT gle.posting_date, gle.company, gle.voucher_type, gle.voucher_no,
		       gle.account, gle.party_type, gle.party, gle.debit, gle.credit, gle.remarks
		FROM `tabGL Entry` gle
		WHERE {" AND ".join(conditions)}
		ORDER BY gle.posting_date DESC, gle.creation DESC
		""",
		values,
		as_dict=True,
	)
	return _columns(), rows


def _columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 140},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 130},
		{"label": _("Voucher"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 160},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 200},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 100},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 150},
		{"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 120},
		{"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 120},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Small Text", "width": 240},
	]
