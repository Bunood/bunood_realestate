# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Customer (tenant) statement of account.

Single source of truth = the ERPNext General Ledger. This report READS `GL Entry`
for the customer party and computes the running balance from it — it never keeps a
parallel balance (that was bunood_core's fatal mistake). Debit = the customer owes
(مدين), Credit = paid/credited (دائن), Balance = the real outstanding.

One report, many scenarios (the "10 reports + 30 filters" doctrine): optional
property / unit / lease filters narrow the ledger to the vouchers of that scope —
the invoices whose lines carry the dimension (or belong to the lease's schedules)
plus the payments/journals that settled them (via Payment Ledger Entry). A shared
settlement voucher is shown in FULL (its receivable row is one GL fact); the aging
summary always reflects the same scope. Aging buckets are computed from the scoped
outstanding invoices as of the To Date.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate


def execute(filters=None):
	filters = filters or {}
	customer = filters.get("customer")
	if not customer:
		return _columns(), []

	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), []

	values = {"customer": customer}
	conditions = ["gle.party_type = 'Customer'", "gle.party = %(customer)s", "gle.is_cancelled = 0"]

	company = filters.get("company")
	if company:
		if company not in allowed:
			frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
		conditions.append("gle.company = %(company)s")
		values["company"] = company
	else:
		conditions.append("gle.company IN %(allowed)s")
		values["allowed"] = tuple(allowed) if len(allowed) > 1 else (allowed[0], allowed[0])

	# Optional real-estate scope → restrict to that scope's vouchers (invoices + their
	# settlements). None = unscoped; empty set = scoped but nothing matches.
	vouchers = _scope_vouchers(customer, filters)
	if vouchers is not None:
		if not vouchers:
			return _columns(), [], None, None, _aging_summary({}, 0.0)
		conditions.append("gle.voucher_no IN %(vouchers)s")
		values["vouchers"] = tuple(vouchers) if len(vouchers) > 1 else (next(iter(vouchers)),) * 2

	base = " AND ".join(conditions)

	# Opening balance = everything strictly before the from-date.
	opening = 0.0
	from_date = filters.get("from_date")
	if from_date:
		values["from_date"] = from_date
		row = frappe.db.sql(
			f"SELECT COALESCE(SUM(gle.debit - gle.credit), 0) FROM `tabGL Entry` gle "
			f"WHERE {base} AND gle.posting_date < %(from_date)s",
			values,
		)
		opening = flt(row[0][0]) if row else 0.0

	period = list(conditions)
	if from_date:
		period.append("gle.posting_date >= %(from_date)s")
	to_date = filters.get("to_date")
	if to_date:
		period.append("gle.posting_date <= %(to_date)s")
		values["to_date"] = to_date

	# Mode of Payment (طريقة الدفع) on every cash line: blank means the payment predates
	# the Phase-0 payment-method discipline (dimension_guard / Real Estate Settings).
	rows = frappe.db.sql(
		f"""
		SELECT gle.posting_date, gle.voucher_type, gle.voucher_no,
		       gle.debit, gle.credit, gle.remarks,
		       pe.mode_of_payment AS payment_method
		FROM `tabGL Entry` gle
		LEFT JOIN `tabPayment Entry` pe
			ON gle.voucher_type = 'Payment Entry' AND pe.name = gle.voucher_no
		WHERE {" AND ".join(period)}
		ORDER BY gle.posting_date ASC, gle.creation ASC
		""",
		values,
		as_dict=True,
	)

	data = []
	balance = opening
	if from_date:
		data.append({
			"posting_date": from_date, "voucher_no": _("Opening Balance"),
			"debit": 0, "credit": 0, "balance": opening,
		})
	for r in rows:
		balance += flt(r.debit) - flt(r.credit)
		r["balance"] = balance
		data.append(r)

	buckets = _aging_buckets(customer, filters, allowed, vouchers)
	return _columns(), data, None, None, _aging_summary(buckets, balance)


def _scope_vouchers(customer, filters):
	"""Voucher names for the property/unit/lease scope: the customer's invoices whose
	LINES carry the dimension (or that belong to the lease's rent/charge schedules and
	termination credits), plus every Payment/Journal voucher that settled them (PLE).
	Returns None when no scope filter is set."""
	prop = filters.get("property")
	unit = filters.get("unit")
	lease = filters.get("lease_contract")
	if not (prop or unit or lease):
		return None

	si_names = set()
	if lease:
		for dt in ("Rent Schedule", "Charge Schedule"):
			si_names.update(
				frappe.get_all(
					dt,
					filters={"lease_contract": lease, "sales_invoice": ["is", "set"]},
					pluck="sales_invoice",
				)
			)
		# Termination credit notes belong to the lease's statement too.
		si_names.update(
			r[0]
			for r in frappe.db.sql(
				"""
				SELECT ltc.credit_note
				FROM `tabLease Termination Credit` ltc
				JOIN `tabLease Termination` lt ON lt.name = ltc.parent
				WHERE lt.lease_contract = %s AND ltc.credit_note IS NOT NULL
				""",
				lease,
			)
		)
	else:
		item_filters = {"docstatus": 1, "customer": customer}
		dim_filters = []
		if prop:
			dim_filters.append(["Sales Invoice Item", "property", "=", prop])
		if unit:
			dim_filters.append(["Sales Invoice Item", "real_estate_unit", "=", unit])
		si_names.update(
			frappe.get_all(
				"Sales Invoice",
				filters=[["docstatus", "=", 1], ["customer", "=", customer]] + dim_filters,
				pluck="name",
			)
		)

	si_names = {s for s in si_names if s}
	if not si_names:
		return set()

	vouchers = set(si_names)
	si_tuple = tuple(si_names) if len(si_names) > 1 else (next(iter(si_names)),) * 2
	settlements = frappe.db.sql(
		"""
		SELECT DISTINCT ple.voucher_no
		FROM `tabPayment Ledger Entry` ple
		WHERE ple.against_voucher_type = 'Sales Invoice'
		  AND ple.against_voucher_no IN %s
		  AND ple.delinked = 0
		  AND ple.voucher_no <> ple.against_voucher_no
		  AND ple.voucher_type IN ('Payment Entry', 'Journal Entry')
		""",
		(si_tuple,),
	)
	vouchers.update(r[0] for r in settlements)
	return vouchers


def _aging_buckets(customer, filters, allowed, vouchers):
	"""Outstanding scoped invoices as of To Date, bucketed by days overdue (due_date)."""
	as_of = getdate(filters.get("to_date") or nowdate())
	inv_filters = [
		["docstatus", "=", 1],
		["customer", "=", customer],
		["outstanding_amount", ">", 0.005],
		["company", "in", [filters.get("company")] if filters.get("company") else allowed],
	]
	if vouchers is not None:
		if not vouchers:
			return {}
		inv_filters.append(["name", "in", list(vouchers)])
	invoices = frappe.get_all(
		"Sales Invoice",
		filters=inv_filters,
		fields=["name", "due_date", "outstanding_amount"],
	)
	buckets = {"current": 0.0, "b30": 0.0, "b60": 0.0, "b90": 0.0, "b90plus": 0.0}
	for inv in invoices:
		overdue = date_diff(as_of, inv.due_date) if inv.due_date else 0
		amt = flt(inv.outstanding_amount)
		if overdue <= 0:
			buckets["current"] += amt
		elif overdue <= 30:
			buckets["b30"] += amt
		elif overdue <= 60:
			buckets["b60"] += amt
		elif overdue <= 90:
			buckets["b90"] += amt
		else:
			buckets["b90plus"] += amt
	return buckets


def _aging_summary(buckets, balance):
	cur = frappe.defaults.get_global_default("currency") or ""

	def card(label, value, indicator):
		return {"label": label, "value": flt(value, 2), "datatype": "Currency", "currency": cur, "indicator": indicator}

	return [
		card(_("Balance"), balance, "Red" if balance > 0 else "Green"),
		card(_("Current (not due)"), buckets.get("current", 0), "Blue"),
		card(_("1–30 days"), buckets.get("b30", 0), "Yellow"),
		card(_("31–60 days"), buckets.get("b60", 0), "Orange"),
		card(_("61–90 days"), buckets.get("b90", 0), "Orange"),
		card(_("90+ days"), buckets.get("b90plus", 0), "Red"),
	]


def _columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 130},
		{"label": _("Voucher"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 160},
		{"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "width": 120},
		{"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "width": 120},
		{"label": _("Payment Method"), "fieldname": "payment_method", "fieldtype": "Link", "options": "Mode of Payment", "width": 130},
		{"label": _("Balance"), "fieldname": "balance", "fieldtype": "Currency", "width": 130},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Small Text", "width": 240},
	]
