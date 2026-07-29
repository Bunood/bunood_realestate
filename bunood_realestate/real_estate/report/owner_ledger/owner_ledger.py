# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Owner Ledger (كشف أستاذ المالك) — the flagship four-section owner statement.

Sections (every line traced to a native voucher; no parallel ledger):
  1. Revenues (الإيرادات)     — CASH collected against the owner's property invoices,
     split Rent vs Charges. Cash-basis on purpose: it mirrors the payout engine
     (management.py), so the owner never sees accrued-but-uncollected money here.
     Source: Payment Ledger Entry settlements, scaled per property net line share —
     the exact frame `_rent_collected_for_property` uses.
  2. Expenses (المصروفات)     — GL debits on Expense accounts tagged with the property
     (maintenance contractor bills, utilities, operating costs). The owner-payout
     expense account is excluded — that posting is the transfer mechanism (section 4),
     not a property operating cost.
  3. Deductions (الاستقطاعات) — management fees from Posted Owner Payout rows, plus
     termination credit notes returned to tenants in the window.
  4. Transfers (التحويلات)    — the owner's REAL supplier ledger: payout accruals
     (credit) and actual payments to the owner (debit, with Mode of Payment /
     طريقة الدفع). Closing balance = صافي مستحق للمالك — exact by construction.

Mode of Payment appears on every cash line (sections 1 & 4) — blank means the payment
predates the Phase-0 discipline (see dimension_guard / Real Estate Settings).
Company-scoped to the caller's permitted companies.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	owner = filters.get("owner")
	prop_filter = filters.get("property")
	if not owner and not prop_filter:
		return _columns(), [], None, None, _summary(0, 0, 0, 0, 0)

	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), [], None, None, _summary(0, 0, 0, 0, 0)

	company = filters.get("company")
	if company and company not in allowed:
		frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
	companies = [company] if company else allowed

	if prop_filter:
		p = frappe.db.get_value("Property", prop_filter, ["name", "owner_party", "company"], as_dict=True)
		if not p:
			frappe.throw(_("Property not found."), frappe.DoesNotExistError)
		if owner and p.owner_party != owner:
			frappe.throw(_("Property {0} does not belong to owner {1}.").format(prop_filter, owner))
		owner = owner or p.owner_party
		properties = [p.name]
	else:
		properties = frappe.get_all(
			"Property",
			filters={"owner_party": owner, "company": ["in", companies]},
			pluck="name",
		)

	from_date = filters.get("from_date")
	to_date = filters.get("to_date")

	data = []
	revenues = expenses = deductions = 0.0

	if properties:
		rev_rows = _revenue_rows(properties, companies, from_date, to_date)
		revenues = sum(flt(r["amount"]) for r in rev_rows)
		data += _section(_("1. Revenues (cash collected)"), rev_rows, revenues)

		exp_rows = _expense_rows(properties, companies, from_date, to_date)
		expenses = sum(flt(r["amount"]) for r in exp_rows)
		data += _section(_("2. Expenses"), exp_rows, expenses)

		ded_rows = _deduction_rows(properties, companies, from_date, to_date)
		deductions = sum(flt(r["amount"]) for r in ded_rows)
		data += _section(_("3. Deductions"), ded_rows, deductions)

	trf_rows, paid, net_due = _transfer_rows(owner, companies, from_date, to_date)
	data += _section(_("4. Transfers to Owner"), trf_rows, paid)
	data.append({
		"section": _("4. Transfers to Owner"),
		"details": _("Net due to owner (صافي مستحق للمالك)"),
		"amount": net_due,
	})

	return _columns(), data, None, None, _summary(revenues, expenses, deductions, paid, net_due)


def _section(title, rows, total):
	"""Tag rows with their section and close with a subtotal line."""
	out = []
	for r in rows:
		r["section"] = title
		out.append(r)
	out.append({"section": title, "details": _("Total — {0}").format(title), "amount": flt(total, 2)})
	return out


def _props_tuple(properties):
	return tuple(properties) if len(properties) > 1 else (properties[0], properties[0])


def _companies_tuple(companies):
	return tuple(companies) if len(companies) > 1 else (companies[0], companies[0])


def _revenue_rows(properties, companies, from_date, to_date):
	"""Cash settlements (PLE frame, identical constraints to management.py) against the
	properties' invoices, split Rent vs Charges by the Default Rent Item, with the
	payment's Mode of Payment (طريقة الدفع)."""
	settings = frappe.get_cached_doc("Real Estate Settings")
	rent_item = settings.default_rent_item or ""
	date_cond = ""
	values = {
		"props": _props_tuple(properties),
		"companies": _companies_tuple(companies),
		"rent_item": rent_item,
		"rent_label": _("Rent"),
		"charge_label": _("Service Charges"),
	}
	if from_date:
		date_cond += " AND ple.posting_date >= %(from_date)s"
		values["from_date"] = from_date
	if to_date:
		date_cond += " AND ple.posting_date <= %(to_date)s"
		values["to_date"] = to_date

	return frappe.db.sql(
		f"""
		SELECT ple.posting_date, ple.voucher_type, ple.voucher_no,
		       pl.property,
		       CASE WHEN pl.kind = 'Rent' THEN %(rent_label)s ELSE %(charge_label)s END AS details,
		       pe.mode_of_payment AS payment_method,
		       SUM((-ple.amount) * (pl.net_p / NULLIF(si.base_grand_total, 0))) AS amount
		FROM `tabPayment Ledger Entry` ple
		JOIN `tabSales Invoice` si ON si.name = ple.against_voucher_no
		JOIN (
			SELECT parent, property,
			       CASE WHEN item_code = %(rent_item)s THEN 'Rent' ELSE 'Charges' END AS kind,
			       SUM(base_net_amount) AS net_p
			FROM `tabSales Invoice Item`
			WHERE property IN %(props)s
			GROUP BY parent, property, kind
		) pl ON pl.parent = si.name
		LEFT JOIN `tabPayment Entry` pe
			ON ple.voucher_type = 'Payment Entry' AND pe.name = ple.voucher_no
		WHERE ple.against_voucher_type = 'Sales Invoice'
		  AND ple.company = si.company
		  AND ple.company IN %(companies)s
		  AND ple.delinked = 0
		  AND ple.voucher_no <> ple.against_voucher_no
		  AND ple.voucher_type IN ('Payment Entry', 'Journal Entry')
		  AND si.docstatus = 1
		  {date_cond}
		GROUP BY ple.posting_date, ple.voucher_type, ple.voucher_no, pl.property, pl.kind,
		         pe.mode_of_payment
		HAVING ABS(SUM((-ple.amount) * (pl.net_p / NULLIF(si.base_grand_total, 0)))) > 0.005
		ORDER BY ple.posting_date ASC, ple.voucher_no ASC
		""",
		values,
		as_dict=True,
	)


def _expense_rows(properties, companies, from_date, to_date):
	"""Property operating costs straight from the GL (dimension-scoped). The payout
	expense account is the transfer mechanism, not an operating cost — excluded."""
	settings = frappe.get_cached_doc("Real Estate Settings")
	conditions = [
		"gle.is_cancelled = 0",
		"gle.property IN %(props)s",
		"gle.company IN %(companies)s",
		"acc.root_type = 'Expense'",
	]
	values = {"props": _props_tuple(properties), "companies": _companies_tuple(companies)}
	if settings.owner_payout_expense_account:
		conditions.append("gle.account <> %(payout_account)s")
		values["payout_account"] = settings.owner_payout_expense_account
	if from_date:
		conditions.append("gle.posting_date >= %(from_date)s")
		values["from_date"] = from_date
	if to_date:
		conditions.append("gle.posting_date <= %(to_date)s")
		values["to_date"] = to_date

	return frappe.db.sql(
		f"""
		SELECT gle.posting_date, gle.voucher_type, gle.voucher_no, gle.property,
		       gle.account AS details,
		       SUM(gle.debit - gle.credit) AS amount
		FROM `tabGL Entry` gle
		JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE {" AND ".join(conditions)}
		GROUP BY gle.posting_date, gle.voucher_type, gle.voucher_no, gle.property, gle.account
		HAVING ABS(SUM(gle.debit - gle.credit)) > 0.005
		ORDER BY gle.posting_date ASC, gle.voucher_no ASC
		""",
		values,
		as_dict=True,
	)


def _deduction_rows(properties, companies, from_date, to_date):
	"""Management fees (Posted Owner Payout) + termination credit notes in the window."""
	rows = []
	op_filters = {
		"status": "Posted",
		"property": ["in", properties],
		"company": ["in", companies],
	}
	if from_date:
		op_filters["to_date"] = [">=", from_date]
	payouts = frappe.get_all(
		"Owner Payout",
		filters=op_filters,
		fields=["name", "property", "from_date", "to_date", "fee_percentage", "fee_amount"],
		order_by="from_date asc",
	)
	for p in payouts:
		if to_date and str(p.from_date) > str(to_date):
			continue
		rows.append({
			"posting_date": p.to_date,
			"voucher_type": "Owner Payout",
			"voucher_no": p.name,
			"property": p.property,
			"details": _("Management fee {0}% ({1} to {2})").format(
				flt(p.fee_percentage), p.from_date, p.to_date
			),
			"amount": flt(p.fee_amount),
		})

	cn_conditions = ["cn.docstatus = 1", "cn.is_return = 1", "sii.property IN %(props)s", "cn.company IN %(companies)s"]
	values = {"props": _props_tuple(properties), "companies": _companies_tuple(companies)}
	if from_date:
		cn_conditions.append("cn.posting_date >= %(from_date)s")
		values["from_date"] = from_date
	if to_date:
		cn_conditions.append("cn.posting_date <= %(to_date)s")
		values["to_date"] = to_date
	credits = frappe.db.sql(
		f"""
		SELECT cn.posting_date, 'Sales Invoice' AS voucher_type, cn.name AS voucher_no,
		       sii.property, SUM(-sii.base_net_amount) AS amount
		FROM `tabSales Invoice` cn
		JOIN `tabSales Invoice Item` sii ON sii.parent = cn.name
		WHERE {" AND ".join(cn_conditions)}
		GROUP BY cn.posting_date, cn.name, sii.property
		ORDER BY cn.posting_date ASC
		""",
		values,
		as_dict=True,
	)
	for c in credits:
		c["details"] = _("Credit note returned to tenant")
		rows.append(c)
	return rows


def _transfer_rows(owner, companies, from_date, to_date):
	"""The owner's real supplier ledger: payout accruals (credit), payments out (debit,
	with Mode of Payment). Returns (rows, total_paid, closing_net_due)."""
	if not owner:
		return [], 0.0, 0.0
	base = [
		"gle.is_cancelled = 0",
		"gle.party_type = 'Supplier'",
		"gle.party = %(owner)s",
		"gle.company IN %(companies)s",
	]
	values = {"owner": owner, "companies": _companies_tuple(companies)}

	opening = 0.0
	rows = []
	if from_date:
		values["from_date"] = from_date
		res = frappe.db.sql(
			f"""SELECT COALESCE(SUM(gle.credit - gle.debit), 0) FROM `tabGL Entry` gle
			WHERE {" AND ".join(base)} AND gle.posting_date < %(from_date)s""",
			values,
		)
		opening = flt(res[0][0]) if res else 0.0
		rows.append({
			"posting_date": from_date,
			"details": _("Opening balance due to owner"),
			"amount": opening,
		})

	period = list(base)
	if from_date:
		period.append("gle.posting_date >= %(from_date)s")
	if to_date:
		period.append("gle.posting_date <= %(to_date)s")
		values["to_date"] = to_date

	gl_rows = frappe.db.sql(
		f"""
		SELECT gle.posting_date, gle.voucher_type, gle.voucher_no, gle.property,
		       gle.debit, gle.credit, pe.mode_of_payment AS payment_method
		FROM `tabGL Entry` gle
		LEFT JOIN `tabPayment Entry` pe
			ON gle.voucher_type = 'Payment Entry' AND pe.name = gle.voucher_no
		WHERE {" AND ".join(period)}
		ORDER BY gle.posting_date ASC, gle.creation ASC
		""",
		values,
		as_dict=True,
	)

	paid = 0.0
	net = opening
	for g in gl_rows:
		signed = flt(g.credit) - flt(g.debit)
		net += signed
		if flt(g.debit) > 0:
			paid += flt(g.debit)
			details = _("Paid to owner")
		else:
			details = _("Payout accrued to owner")
		rows.append({
			"posting_date": g.posting_date,
			"voucher_type": g.voucher_type,
			"voucher_no": g.voucher_no,
			"property": g.property,
			"details": details,
			"payment_method": g.payment_method,
			"amount": signed,
		})
	return rows, flt(paid, 2), flt(net, 2)


def _summary(revenues, expenses, deductions, paid, net_due):
	cur = frappe.defaults.get_global_default("currency") or ""
	return [
		{"label": _("Revenues (collected)"), "value": flt(revenues, 2), "datatype": "Currency", "currency": cur, "indicator": "Blue"},
		{"label": _("Expenses"), "value": flt(expenses, 2), "datatype": "Currency", "currency": cur, "indicator": "Orange"},
		{"label": _("Deductions"), "value": flt(deductions, 2), "datatype": "Currency", "currency": cur, "indicator": "Orange"},
		{"label": _("Paid to Owner"), "value": flt(paid, 2), "datatype": "Currency", "currency": cur, "indicator": "Green"},
		{"label": _("Net Due to Owner"), "value": flt(net_due, 2), "datatype": "Currency", "currency": cur, "indicator": "Red" if net_due > 0 else "Green"},
	]


def _columns():
	return [
		{"label": _("Section"), "fieldname": "section", "fieldtype": "Data", "width": 200},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 120},
		{"label": _("Voucher"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 160},
		{"label": _("Property"), "fieldname": "property", "fieldtype": "Link", "options": "Property", "width": 150},
		{"label": _("Details"), "fieldname": "details", "fieldtype": "Data", "width": 260},
		{"label": _("Payment Method"), "fieldname": "payment_method", "fieldtype": "Link", "options": "Mode of Payment", "width": 130},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 140},
	]
