# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Management model (إدارة أملاك, behavior='managed') owner accounting.

The company collects rent from tenants (rent Sales Invoice), but KEEPS only its
management fee %; the rest is owed to the owner. This posts the owner payout:

    Dr  Owner Payout Expense           (rent × (1 − fee%))
    Cr  Creditors  (party = owner Supplier)

Net company income for the property = the management fee %. All via ERPNext native
docs (no parallel ledger). Profit visible in P&L by the Property dimension.

Idempotency: every payout is persisted as an `Owner Payout` record whose
(property, from_date, to_date) window is the natural key. Before posting we take a
`for_update` lock on the Property row and refuse any window that OVERLAPS an already
Posted payout — so a re-run, a double-click, a wide/overlapping window, or two
concurrent operators can never double-pay the owner.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


def compute_owner_payout(rent_base, fee_pct):
	"""Pure & testable: split the collected rent into (company fee, owner payout)."""
	rent_base = flt(rent_base)
	fee_pct = flt(fee_pct)
	fee = flt(rent_base * fee_pct / 100.0, 2)
	owner = flt(rent_base - fee, 2)
	return {"rent_base": flt(rent_base, 2), "fee": fee, "owner_payout": owner}


def _rent_income_for_property(property, from_date, to_date):
	"""Net (pre-VAT) rent income tagged with the Property accounting dimension in the period.
	Fee-charge invoices are NOT tagged with the Property dimension, so this captures rent only."""
	res = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(sii.base_net_amount), 0)
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1
		  AND si.posting_date BETWEEN %s AND %s
		  AND sii.property = %s
		""",
		(from_date, to_date, property),
	)
	return flt(res[0][0]) if res else 0.0


def _overlapping_payout(property, from_date, to_date):
	"""Return an existing Posted payout whose period overlaps [from_date, to_date], if any.
	Locking read (FOR UPDATE): serialized behind the Property row lock, the second caller
	sees the first caller's committed row and is blocked."""
	rows = frappe.db.sql(
		"""
		SELECT name, from_date, to_date
		FROM `tabOwner Payout`
		WHERE property = %s AND status = 'Posted'
		  AND from_date <= %s AND to_date >= %s
		ORDER BY from_date ASC LIMIT 1
		FOR UPDATE
		""",
		(property, to_date, from_date),
		as_dict=True,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def generate_owner_payout(property, from_date, to_date):
	"""Post the owner payout for a managed property over a period. Requires JE submit rights."""
	frappe.has_permission("Journal Entry", "submit", throw=True)

	from_date, to_date = getdate(from_date), getdate(to_date)
	if from_date > to_date:
		frappe.throw(_("From Date must be on or before To Date."))

	p = frappe.get_doc("Property", property)

	behavior = frappe.db.get_value("RE Management Model", p.management_model, "behavior") if p.management_model else None
	if behavior != "managed":
		frappe.throw(_("Owner payout applies only to Managed (إدارة أملاك) properties."))
	if not p.owner_party:
		frappe.throw(_("Set the Owner (Supplier) on the property first."))

	fee_pct = flt(p.management_fee_percentage)
	if fee_pct <= 0:
		frappe.throw(_("Set a positive Management Fee % on the property (a managed property keeps a fee)."))

	settings = frappe.get_single("Real Estate Settings")
	if not settings.owner_payout_expense_account:
		frappe.throw(_("Set the Owner Payout Expense Account in Real Estate Settings."))
	payable = frappe.get_cached_value("Company", p.company, "default_payable_account")
	if not payable:
		frappe.throw(_("Set a Default Payable Account on the company."))

	# Serialize all payouts for this property (every payout locks the same Property row),
	# then refuse any window overlapping an already-Posted payout — the idempotency guard.
	frappe.db.get_value("Property", property, "name", for_update=True)
	clash = _overlapping_payout(property, from_date, to_date)
	if clash:
		frappe.throw(
			_("Owner payout {0} already covers {1} to {2} for this property — periods must not overlap.").format(
				clash.name, clash.from_date, clash.to_date
			)
		)

	rent_base = _rent_income_for_property(property, from_date, to_date)
	if rent_base <= 0:
		frappe.throw(_("No rent income found for this property in the selected period."))

	calc = compute_owner_payout(rent_base, fee_pct)
	payout = calc["owner_payout"]
	if payout <= 0:
		frappe.throw(_("Computed owner payout is zero."))

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = p.company
	je.multi_currency = 0
	je.posting_date = to_date or nowdate()
	je.user_remark = _("Owner payout — {0} ({1} to {2}), fee {3}%").format(
		property, from_date, to_date, fee_pct
	)
	je.append("accounts", {
		"account": settings.owner_payout_expense_account,
		"debit_in_account_currency": payout,
	})
	je.append("accounts", {
		"account": payable,
		"party_type": "Supplier",
		"party": p.owner_party,
		"credit_in_account_currency": payout,
	})
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()

	# Persist the payout — this row is the idempotency key for future runs.
	doc = frappe.get_doc({
		"doctype": "Owner Payout",
		"property": property,
		"owner_party": p.owner_party,
		"company": p.company,
		"from_date": from_date,
		"to_date": to_date,
		"rent_base": calc["rent_base"],
		"fee_percentage": fee_pct,
		"fee_amount": calc["fee"],
		"owner_payout": payout,
		"journal_entry": je.name,
		"status": "Posted",
	})
	doc.flags.ignore_permissions = True
	doc.insert()

	return {"journal_entry": je.name, "owner_payout_record": doc.name, **calc}
