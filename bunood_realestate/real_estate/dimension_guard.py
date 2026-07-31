# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Phase 0 of the financial information layer (docs/plan-financial-reporting.md):
dimension enforcement as a pure in-app validation layer — never a core change.

Any financial document that BELONGS to real estate must carry the Property dimension
(and a consistent Unit) before it reaches the GL, otherwise every dimension-based
report (Owner Ledger, Property P&L, statements) silently under-reports. "Belongs to
real estate" is decided per ROW: the row already carries a property/unit, or its
account is one of the configured real-estate accounts (Real Estate Settings + the
Charge Type income accounts).

Payment Entries get two extra disciplines (they carry the cash):
  * if the payment settles a real-estate invoice, the payment itself must be tagged
    with the property so cash reports stay dimension-complete;
  * a real-estate payment must state its Mode of Payment (طريقة الدفع) — statements
    and the Owner Ledger show HOW every riyal moved, so blank modes are data loss.

Enforcement level comes from Real Estate Settings (`dimension_enforcement`):
Off = do nothing, Warn = msgprint (default — safe for existing sites), Block = throw.
Historic gaps are surfaced by the "Missing Dimension Audit" report; flip to Block
only after that report runs clean.
"""

import frappe
from frappe import _

# Child-table + account-field map per hooked doctype. Dimension custom fields
# (property / real_estate_unit) are created by the Accounting Dimension on both the
# parents and these children; `.get()` keeps us safe if a site has not migrated yet.
_ROW_SOURCES = {
	"Sales Invoice": [("items", "income_account")],
	"Purchase Invoice": [("items", "expense_account")],
	"Journal Entry": [("accounts", "account")],
	"Expense Claim": [("expenses", "default_account")],
	# Payment Entry rows: deductions can hit RE expense accounts directly.
	"Payment Entry": [("deductions", "account")],
}


def _settings():
	try:
		return frappe.get_cached_doc("Real Estate Settings")
	except Exception:
		return None


def re_account_set(settings):
	"""All accounts that mark a row as real-estate money. Config-over-code: the set is
	whatever the site configured (plus every Charge Type income account) — nothing hardcoded."""
	accounts = {
		settings.get(f)
		for f in (
			"rent_income_account",
			"maintenance_expense_account",
			"owner_payout_expense_account",
			"tenant_deposit_account",
			"deduction_income_account",
			"opening_balance_account",
		)
		if settings.get(f)
	}
	# Multi-company: every profile's accounts guard too (account names are site-unique
	# in ERPNext, so the cross-company union only ADDS discipline, never mixes books).
	try:
		from bunood_realestate.real_estate.company_settings import all_configured_values

		for f in (
			"rent_income_account",
			"maintenance_expense_account",
			"owner_payout_expense_account",
			"tenant_deposit_account",
			"deduction_income_account",
			"opening_balance_account",
		):
			accounts.update(all_configured_values(f))
	except Exception:
		pass  # guard must never break validation (mirrors the Charge Type try/except below)
	try:
		accounts.update(
			frappe.get_all(
				"Charge Type",
				filters={"income_account": ["is", "set"]},
				pluck="income_account",
				distinct=True,
			)
		)
	except Exception:
		# Charge Type may not exist mid-install; the settings accounts still guard.
		pass
	return accounts


def check_rows(rows, re_accounts, unit_property):
	"""Pure & testable core. ``rows`` = [{idx, account, property, real_estate_unit}],
	``unit_property`` = {unit name: its property}. Returns a list of problem strings."""
	problems = []
	for r in rows:
		prop = r.get("property")
		unit = r.get("real_estate_unit")
		is_re = bool(prop or unit or (r.get("account") in re_accounts))
		if not is_re:
			continue
		if not prop:
			problems.append(
				_("Row {0}: this is a real-estate row ({1}) — set the Property dimension.").format(
					r.get("idx"), r.get("account") or _("unit-tagged")
				)
			)
		if unit and prop and unit_property.get(unit) and unit_property[unit] != prop:
			problems.append(
				_("Row {0}: Unit {1} belongs to property {2}, not {3}.").format(
					r.get("idx"), unit, unit_property[unit], prop
				)
			)
	return problems


def _collect_rows(doc):
	rows = []
	for table_field, account_field in _ROW_SOURCES.get(doc.doctype, []):
		for row in doc.get(table_field) or []:
			rows.append(
				{
					"idx": row.get("idx"),
					"account": row.get(account_field),
					"property": row.get("property") or doc.get("property"),
					"real_estate_unit": row.get("real_estate_unit") or doc.get("real_estate_unit"),
				}
			)
	return rows


def _unit_property_map(rows):
	units = {r["real_estate_unit"] for r in rows if r.get("real_estate_unit")}
	if not units:
		return {}
	return dict(
		frappe.get_all(
			"Real Estate Unit",
			filters={"name": ["in", list(units)]},
			fields=["name", "property"],
			as_list=True,
		)
	)


def _payment_entry_problems(doc, settings):
	"""RE-payment discipline: property tag carried from the settled invoices, and an
	explicit Mode of Payment (طريقة الدفع) so cash reports never show a blank method."""
	problems = []
	invoices = {"Sales Invoice": [], "Purchase Invoice": []}
	for ref in doc.get("references") or []:
		if ref.get("reference_doctype") in invoices and ref.get("reference_name"):
			invoices[ref.reference_doctype].append(ref.reference_name)

	ref_properties = set()
	for dt, names in invoices.items():
		if not names:
			continue
		ref_properties.update(
			frappe.get_all(
				f"{dt} Item",
				filters={"parent": ["in", names], "property": ["is", "set"]},
				pluck="property",
				distinct=True,
			)
		)

	if ref_properties:
		if not doc.get("property"):
			problems.append(
				_("This payment settles real-estate invoice(s) of {0} — set the Property dimension on the payment.").format(
					", ".join(sorted(ref_properties))
				)
			)
		elif doc.get("property") not in ref_properties and len(ref_properties) == 1:
			problems.append(
				_("Payment Property {0} does not match the invoices' property {1}.").format(
					doc.get("property"), next(iter(ref_properties))
				)
			)

	is_re_payment = bool(ref_properties or doc.get("property"))
	if is_re_payment and settings.get("require_mode_of_payment") and not doc.get("mode_of_payment"):
		problems.append(
			_("Real-estate payments must state a Mode of Payment (طريقة الدفع) — cash, transfer, cheque or POS — so statements can show how the money moved.")
		)
	return problems


def validate_dimensions(doc, method=None):
	"""doc_events → validate on Sales/Purchase Invoice, Journal Entry, Payment Entry,
	Expense Claim. In-app only; the accounting engine is never touched."""
	settings = _settings()
	if not settings:
		return
	mode = settings.get("dimension_enforcement") or "Off"
	if mode == "Off":
		return

	rows = _collect_rows(doc)
	problems = check_rows(rows, re_account_set(settings), _unit_property_map(rows))
	if doc.doctype == "Payment Entry":
		problems += _payment_entry_problems(doc, settings)

	if not problems:
		return
	message = "<br>".join(problems)
	if mode == "Block":
		frappe.throw(message, title=_("Real Estate Dimension Check"))
	frappe.msgprint(
		message,
		title=_("Real Estate Dimension Check"),
		indicator="orange",
	)
