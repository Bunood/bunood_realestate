# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Management model (إدارة أملاك, behavior='managed') owner accounting.

The company collects rent from tenants (rent Sales Invoice), but KEEPS only its
management fee %; the rest is owed to the owner. Owner accounting is CASH-BASIS: the
payout is a share of rent actually COLLECTED in the window (see
``_rent_collected_for_property``), so the company never remits money it has not yet
received. This posts the owner payout:

    Dr  Owner Payout Expense           (collected rent × (1 − fee%))
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

from bunood_realestate.real_estate.gl_utils import assert_company_access, require_cost_center


def compute_owner_payout(rent_base, fee_pct):
	"""Pure & testable: split the collected rent into (company fee, owner payout)."""
	rent_base = flt(rent_base)
	fee_pct = flt(fee_pct)
	fee = flt(rent_base * fee_pct / 100.0, 2)
	owner = flt(rent_base - fee, 2)
	return {"rent_base": flt(rent_base, 2), "fee": fee, "owner_payout": owner}


def _rent_collected_for_property(property, from_date, to_date, rent_income_account, rent_item):
	"""Net (pre-VAT) rent CASH COLLECTED for this property's rent invoices in the window.

	Cash-basis owner accounting (the solid-foundation choice): the owner is paid a share of
	what the tenant ACTUALLY PAID — never merely what was invoiced — so the company never
	remits money it has not received.

	Source is ERPNext's Payment Ledger Entry, the single native record of every settlement
	against a receivable. Using it (rather than only Payment Entry Reference) means:
	  * BOTH Payment Entry receipts AND Journal Entry receipts count — a JE "Dr Bank / Cr
	    Debtors" against the rent invoice is a first-class collection route.
	  * amounts are in COMPANY currency (``ple.amount``), immune to the multi-currency
	    mis-denomination a party-currency ``allocated_amount`` sum would suffer.
	We keep only: settlement rows (``voucher_no`` <> the invoice's own booking row), from
	cash vouchers (Payment/Journal Entry — excludes credit-note applications, which are not
	cash), not delinked (so a cancelled/reversed payment drops out), and scale each by the
	PROPERTY's own RENT net line share on that invoice (line-exact + VAT-stripped) so a
	multi-property invoice attributes only its P-lines' cash to P. Settlements reduce a
	receivable (negative ``amount``), hence ``-ple.amount``.

	CRITICAL: rent lines are identified POSITIVELY — ``item_code = rent_item`` (rent invoices
	always use the Default Rent Item; the charge engine hard-refuses to bill that item) AND
	``income_account = rent_income_account``. Account equality alone is NOT a safe
	discriminator: a utility charge line with no explicit revenue account can resolve (via
	item/company defaults) to the very same income account as rent, and its property-tagged
	cash would then be folded into the owner's rent base (over-pay). The item filter closes
	that hole; the account filter stays as defense-in-depth."""
	res = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(
			(-ple.amount) * (pl.net_p / NULLIF(si.base_grand_total, 0))
		), 0)
		FROM `tabPayment Ledger Entry` ple
		JOIN `tabSales Invoice` si ON si.name = ple.against_voucher_no
		JOIN (
			SELECT parent, SUM(base_net_amount) AS net_p
			FROM `tabSales Invoice Item`
			WHERE property = %(property)s
			  AND item_code = %(rent_item)s
			  AND income_account = %(rent_income_account)s
			GROUP BY parent
		) pl ON pl.parent = si.name
		WHERE ple.against_voucher_type = 'Sales Invoice'
		  AND ple.company = si.company
		  AND ple.delinked = 0
		  AND ple.voucher_no <> ple.against_voucher_no
		  AND ple.voucher_type IN ('Payment Entry', 'Journal Entry')
		  AND ple.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  AND si.docstatus = 1
		""",
		{
			"property": property, "from_date": from_date, "to_date": to_date,
			"rent_income_account": rent_income_account, "rent_item": rent_item,
		},
	)
	return flt(res[0][0]) if res else 0.0


def _collected_net_for_invoice(si_name, to_date, property, rent_income_account, rent_item):
	"""Net-scaled cash collected against ONE invoice up to ``to_date`` (same PLE frame as
	:func:`_rent_collected_for_property`, unbounded start — cash to date)."""
	res = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(
			(-ple.amount) * (pl.net_p / NULLIF(si.base_grand_total, 0))
		), 0)
		FROM `tabPayment Ledger Entry` ple
		JOIN `tabSales Invoice` si ON si.name = ple.against_voucher_no
		JOIN (
			SELECT parent, SUM(base_net_amount) AS net_p
			FROM `tabSales Invoice Item`
			WHERE property = %(property)s
			  AND item_code = %(rent_item)s
			  AND income_account = %(rent_income_account)s
			GROUP BY parent
		) pl ON pl.parent = si.name
		WHERE si.name = %(si_name)s
		  AND ple.against_voucher_type = 'Sales Invoice'
		  AND ple.company = si.company
		  AND ple.delinked = 0
		  AND ple.voucher_no <> ple.against_voucher_no
		  AND ple.voucher_type IN ('Payment Entry', 'Journal Entry')
		  AND ple.posting_date <= %(to_date)s
		""",
		{
			"si_name": si_name, "to_date": to_date, "property": property,
			"rent_income_account": rent_income_account, "rent_item": rent_item,
		},
	)
	return flt(res[0][0]) if res else 0.0


def _credited_rent_for_property(property, from_date, to_date, rent_income_account, rent_item):
	"""Ex-VAT rent CREDITED BACK to tenants (termination credit notes posted in the window),
	each capped at the cash actually collected on its originating invoice.

	Why: a standalone credit note writes no settlement PLE row the collected query can see,
	so a paid-then-credited period would otherwise pay the owner on money the company now
	owes back to the tenant. The cap keeps the other direction safe too — crediting a NEVER
	PAID invoice must not reduce the owner base below the cash actually held."""
	rows = frappe.db.sql(
		"""
		SELECT ltc.sales_invoice AS orig, cn.name AS cn
		FROM `tabLease Termination Credit` ltc
		JOIN `tabSales Invoice` cn ON cn.name = ltc.credit_note
		WHERE cn.docstatus = 1 AND cn.is_return = 1
		  AND cn.posting_date BETWEEN %(from_date)s AND %(to_date)s
		""",
		{"from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	total = 0.0
	for r in rows:
		credit_net = flt(
			frappe.db.sql(
				"""
				SELECT COALESCE(SUM(-base_net_amount), 0)
				FROM `tabSales Invoice Item`
				WHERE parent = %s AND property = %s AND item_code = %s AND income_account = %s
				""",
				(r.cn, property, rent_item, rent_income_account),
			)[0][0]
		)
		if credit_net <= 0:
			continue
		collected = _collected_net_for_invoice(
			r.orig, to_date, property, rent_income_account, rent_item
		)
		total += min(credit_net, max(0.0, collected))
	return flt(total, 2)


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
	# Record/company scope: the doctype-level JE-submit right above does NOT apply
	# Company User Permissions, and we post with ignore_permissions — so verify the
	# caller may act in this property's company before touching the GL.
	assert_company_access(p.company)

	behavior = frappe.db.get_value("RE Management Model", p.management_model, "behavior") if p.management_model else None
	if behavior != "managed":
		frappe.throw(_("Owner payout applies only to Managed (إدارة أملاك) properties."))
	if not p.owner_party:
		frappe.throw(_("Set the Owner (Supplier) on the property first."))

	fee_pct = flt(p.management_fee_percentage)
	if fee_pct <= 0:
		frappe.throw(_("Set a positive Management Fee % on the property (a managed property keeps a fee)."))

	from bunood_realestate.real_estate.company_settings import require_company_config

	settings = require_company_config(
		p.company,
		["owner_payout_expense_account", "rent_income_account", "default_rent_item"],
	)
	if not settings.owner_payout_expense_account:
		frappe.throw(_("Set the Owner Payout Expense Account in Real Estate Settings."))
	if not settings.rent_income_account:
		# Needed to identify RENT cash (vs utility/service charge cash) in _rent_collected_for_property.
		frappe.throw(_("Set the Rent Income Account in Real Estate Settings (used to separate rent cash from other charges)."))
	if not settings.default_rent_item:
		# The rent Service Item is the positive rent-line discriminator in the collected-cash query.
		frappe.throw(_("Set the Default Rent Item in Real Estate Settings (used to identify rent lines for the owner payout)."))
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

	rent_base = _rent_collected_for_property(
		property, from_date, to_date, settings.rent_income_account, settings.default_rent_item
	) - _credited_rent_for_property(
		property, from_date, to_date, settings.rent_income_account, settings.default_rent_item
	)
	if rent_base <= 0:
		frappe.throw(_("No rent was collected for this property in the selected period (owner payout is cash-basis — paid on collected rent, not merely invoiced)."))

	calc = compute_owner_payout(rent_base, fee_pct)
	payout = calc["owner_payout"]
	if payout <= 0:
		frappe.throw(_("Computed owner payout is zero."))

	# P&L expense line needs a company-matching cost center; tag it with the Property
	# dimension so the payout offsets that property's rent income (net P&L = the fee).
	cost_center = require_cost_center(p.company)

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
		"property": property,
		"cost_center": cost_center,
	})
	je.append("accounts", {
		"account": payable,
		"party_type": "Supplier",
		"party": p.owner_party,
		"credit_in_account_currency": payout,
		"property": property,
		"cost_center": cost_center,
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
