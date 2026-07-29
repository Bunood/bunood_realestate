# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""RE VAT Summary (ملخص ضريبة القيمة المضافة العقاري) — the filing-period view.

Saudi VAT for real estate: commercial rent = 15%, residential rent = exempt — the
lease/charge engines stamp the right Sales Taxes template per contract kind, and both
generators keep every invoice TAX-HOMOGENEOUS (one template per invoice), so grouping
whole invoices by template is exact for generated documents.

  * Output VAT (sales): the company's real-estate Sales Invoices (any property-tagged
    line), grouped by tax template — count, net, VAT, gross. Credit notes net off
    naturally (negative sums). Opening-balance invoices are excluded (not turnover).
  * Input VAT (purchases): real-estate Purchase Invoices (contractor/maintenance/head-
    lease), same grouping — the recoverable side.
  * Summary: Output VAT − Input VAT = net VAT position for the period.

v1 limitation (deliberate): a MANUAL mixed invoice (RE + non-RE lines) is counted
whole. The Phase-0 dimension guard pushes such documents to carry dimensions
correctly; ZATCA e-invoicing integration is a separate later phase.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	allowed = frappe.get_list("Company", pluck="name") or []
	if not allowed:
		return _columns(), []

	company = filters.get("company")
	if company and company not in allowed:
		frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
	companies = [company] if company else allowed

	sales = _grouped(
		"Sales Invoice", "Sales Invoice Item", companies,
		filters.get("from_date"), filters.get("to_date"),
	)
	purchases = _grouped(
		"Purchase Invoice", "Purchase Invoice Item", companies,
		filters.get("from_date"), filters.get("to_date"),
	)

	data = []
	out_net = out_tax = in_net = in_tax = 0.0
	for r in sales:
		r["section"] = _("Output (Sales)")
		out_net += flt(r["net"])
		out_tax += flt(r["tax"])
		data.append(r)
	data.append({"section": _("Output (Sales)"), "tax_template": _("Total Output"),
	             "net": flt(out_net, 2), "tax": flt(out_tax, 2), "gross": flt(out_net + out_tax, 2)})
	for r in purchases:
		r["section"] = _("Input (Purchases)")
		in_net += flt(r["net"])
		in_tax += flt(r["tax"])
		data.append(r)
	data.append({"section": _("Input (Purchases)"), "tax_template": _("Total Input"),
	             "net": flt(in_net, 2), "tax": flt(in_tax, 2), "gross": flt(in_net + in_tax, 2)})

	return _columns(), data, None, None, _summary(out_tax, in_tax)


def _grouped(doctype, item_doctype, companies, from_date, to_date):
	"""Whole real-estate invoices (any property-tagged line, opening excluded),
	grouped by tax template. Tax-homogeneous by construction for generated docs."""
	conditions = [
		"inv.docstatus = 1",
		"inv.company IN %(companies)s",
		"COALESCE(inv.is_opening, 'No') != 'Yes'",
		f"""EXISTS (
			SELECT 1 FROM `tab{item_doctype}` it
			WHERE it.parent = inv.name AND COALESCE(it.property, '') != ''
		)""",
	]
	values = {"companies": tuple(companies) if len(companies) > 1 else (companies[0], companies[0])}
	if from_date:
		conditions.append("inv.posting_date >= %(from_date)s")
		values["from_date"] = from_date
	if to_date:
		conditions.append("inv.posting_date <= %(to_date)s")
		values["to_date"] = to_date

	return frappe.db.sql(
		f"""
		SELECT COALESCE(inv.taxes_and_charges, '') AS tax_template,
		       COUNT(*) AS invoices,
		       SUM(inv.base_net_total) AS net,
		       SUM(inv.base_total_taxes_and_charges) AS tax,
		       SUM(inv.base_grand_total) AS gross
		FROM `tab{doctype}` inv
		WHERE {" AND ".join(conditions)}
		GROUP BY inv.taxes_and_charges
		ORDER BY tax DESC
		""",
		values,
		as_dict=True,
	)


def _summary(out_tax, in_tax):
	cur = frappe.defaults.get_global_default("currency") or ""
	net = flt(out_tax - in_tax, 2)
	return [
		{"label": _("Output VAT"), "value": flt(out_tax, 2), "datatype": "Currency", "currency": cur, "indicator": "Blue"},
		{"label": _("Input VAT (recoverable)"), "value": flt(in_tax, 2), "datatype": "Currency", "currency": cur, "indicator": "Green"},
		{"label": _("Net VAT Payable"), "value": net, "datatype": "Currency", "currency": cur, "indicator": "Red" if net > 0 else "Green"},
	]


def _columns():
	return [
		{"label": _("Section"), "fieldname": "section", "fieldtype": "Data", "width": 150},
		{"label": _("Tax Template"), "fieldname": "tax_template", "fieldtype": "Data", "width": 280},
		{"label": _("Invoices"), "fieldname": "invoices", "fieldtype": "Int", "width": 90},
		{"label": _("Net (ex-VAT)"), "fieldname": "net", "fieldtype": "Currency", "width": 140},
		{"label": _("VAT"), "fieldname": "tax", "fieldtype": "Currency", "width": 130},
		{"label": _("Gross"), "fieldname": "gross", "fieldtype": "Currency", "width": 140},
	]
