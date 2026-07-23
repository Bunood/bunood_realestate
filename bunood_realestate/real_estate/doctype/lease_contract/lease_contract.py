# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, date_diff, flt, getdate

# ZATCA VAT number: 15 digits, starts and ends with 3 (bunood_core parity).
ZATCA_VAT_RE = re.compile(r"^3\d{13}3$")

# One-time fee field -> Bunood Core Charge Type. On activation each non-zero fee
# becomes a pending Charge; the "Post Fee Charges" action turns them into a Sales
# Invoice (event -> Charge -> ERPNext, never Contract -> Sales Invoice directly).
FEE_CHARGES = {
	"brokerage_fee": "Broker Fee",
	"general_services_amount": "General Services",
	"waste_removal_fee": "Waste Removal",
	"engineering_supervision_fee": "Engineering Supervision",
	"unit_finishing_fee": "Unit Finishing",
}


class LeaseContract(Document):
	def validate(self):
		self._compute_totals()
		self._validate_dates()
		self._validate_commercial_vat()
		self._guard_unit_overlap()

	def _compute_totals(self):
		"""annual_rent_total is the single source of truth = sum of unit annual rents."""
		self.annual_rent_total = sum(flt(row.annual_rent) for row in self.units)

	def _validate_dates(self):
		if self.start_date and self.end_date and getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("End Date cannot be before Start Date."))

	def _validate_commercial_vat(self):
		"""Commercial leases require a valid ZATCA tenant VAT number; residential are unaffected."""
		if self.contract_type != "Commercial":
			return
		vat = (self.tenant_vat_number or "").strip()
		if not vat:
			frappe.throw(_("A Tenant VAT Number is required for a Commercial lease."))
		if not ZATCA_VAT_RE.match(vat):
			frappe.throw(
				_("Tenant VAT Number must be 15 digits starting and ending with 3 (ZATCA format).")
			)

	def _guard_unit_overlap(self):
		"""A unit cannot be in two overlapping ACTIVE (submitted) leases."""
		for row in self.units:
			if not row.unit:
				continue
			# On submit, lock the unit row so two concurrent submits serialize; the loser
			# then sees the winner as Active and is rejected (prevents double-booking).
			if self.docstatus == 1:
				frappe.db.get_value("Real Estate Unit", row.unit, "name", for_update=True)
			clashes = frappe.db.sql(
				"""
				SELECT lc.name
				FROM `tabLease Contract` lc
				JOIN `tabLease Unit` lu ON lu.parent = lc.name
				WHERE lu.unit = %(unit)s
				  AND lc.name != %(self_name)s
				  AND lc.docstatus = 1
				  AND lc.status = 'Active'
				  AND lc.start_date <= %(end_date)s
				  AND lc.end_date >= %(start_date)s
				LIMIT 1
				""",
				{
					"unit": row.unit,
					"self_name": self.name or "",
					"start_date": self.start_date,
					"end_date": self.end_date,
				},
			)
			if clashes:
				frappe.throw(
					_("Unit {0} is already leased under active contract {1} for an overlapping period.").format(
						frappe.bold(row.unit), frappe.bold(clashes[0][0])
					)
				)

	def on_submit(self):
		from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import generate_for_lease

		self.db_set("status", "Active")
		self._set_units_status("Occupied", current_lease=self.name)
		# Generate the full planned rent schedule (due dates + prorated installments).
		generate_for_lease(self)
		# A submitted renewal marks its parent contract Renewed.
		if self.contract_subtype == "Renewal" and self.parent_lease:
			frappe.db.set_value("Lease Contract", self.parent_lease, "status", "Renewed")
		# Raise one-time fees as pending Charges (Bunood Core engine).
		self._raise_fee_charges()

	def on_cancel(self):
		from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import cancel_for_lease

		self._block_cancel_if_invoiced()
		self.db_set("status", "Cancelled")
		self._set_units_status("Vacant", current_lease=None)
		cancel_for_lease(self)
		self._cancel_fee_charges()

	def _raise_fee_charges(self):
		"""One-time fee fields -> pending Charges (decoupled from accounting). Uses the
		internal _apply (the submitter may lack Charge-create rights) and tags each charge
		with the correct VAT template (residential exempt / commercial 15%)."""
		from bunood_realestate.core import charge

		settings = frappe.get_single("Real Estate Settings")
		tax_template = (
			settings.commercial_tax_template
			if self.contract_type == "Commercial"
			else settings.residential_tax_template
		)
		for field, ctype in FEE_CHARGES.items():
			amt = flt(self.get(field))
			if amt > 0:
				charge._apply(
					charge_type=ctype,
					party=self.customer,
					party_type="Customer",
					amount=amt,
					company=self.company,
					reference_doctype="Lease Contract",
					reference_name=self.name,
					remarks=f"{ctype} — {self.name}",
					tax_template=tax_template,
				)

	def _cancel_fee_charges(self):
		"""Cancel still-pending fee charges when the lease is cancelled."""
		pending = frappe.get_all(
			"Charge",
			filters={
				"reference_doctype": "Lease Contract",
				"reference_name": self.name,
				"status": "Pending",
			},
			pluck="name",
		)
		for name in pending:
			frappe.db.set_value("Charge", name, "status", "Cancelled")

	def _block_cancel_if_invoiced(self):
		"""Don't orphan issued invoices: require they be cancelled/credited first, or use
		Terminate. Cancel is for a lease created by mistake, before any real billing."""
		live = frappe.db.sql(
			"""
			SELECT si.name
			FROM `tabRent Schedule` rs
			JOIN `tabSales Invoice` si ON si.name = rs.sales_invoice
			WHERE rs.lease_contract = %s AND si.docstatus = 1
			LIMIT 1
			""",
			self.name,
		)
		if not live:
			# Also block if a posted FEE-charge invoice exists (via the Charge engine).
			live = frappe.db.sql(
				"""
				SELECT si.name
				FROM `tabCharge` c
				JOIN `tabSales Invoice` si ON si.name = c.sales_invoice
				WHERE c.reference_doctype = 'Lease Contract'
				  AND c.reference_name = %s
				  AND si.docstatus = 1
				LIMIT 1
				""",
				self.name,
			)
		if live:
			frappe.throw(
				_("Cancel or credit the issued Sales Invoice(s) first, or use Terminate instead.")
			)

	def _set_units_status(self, status, current_lease):
		for row in self.units:
			if not row.unit:
				continue
			frappe.db.set_value(
				"Real Estate Unit",
				row.unit,
				{"status": status, "current_lease": current_lease},
			)


@frappe.whitelist()
def renew_lease(lease_contract, rent_bump_pct=0, months=None):
	"""Create a Draft renewal: same units/terms, dates shifted to follow the old term,
	rent bumped by rent_bump_pct (the only place rent increases — no mid-contract escalation)."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	src = frappe.get_doc("Lease Contract", lease_contract)

	new = frappe.copy_doc(src)
	new.parent_lease = src.name
	new.contract_subtype = "Renewal"
	new.status = "Draft"

	start = add_days(src.end_date, 1)
	if months:
		end = add_days(add_months(start, int(months)), -1)
	else:
		end = add_days(start, date_diff(src.end_date, src.start_date))
	new.start_date = start
	new.end_date = end

	factor = (100 + flt(rent_bump_pct)) / 100.0
	for u in new.units:
		u.annual_rent = flt(u.annual_rent) * factor

	# Fresh deposit tracking for the new contract.
	for f in (
		"deposit_received",
		"deposit_received_date",
		"deposit_journal_entry",
		"deposit_refunded",
		"deposit_refund_journal_entry",
	):
		new.set(f, None)

	new.insert(ignore_permissions=True)
	return new.name


# ---------------------------------------------------------------------------
# New-lease wizard (guided 7-step creation) — mirrors the bunood_core Ejar wizard.
# ---------------------------------------------------------------------------

# Scalar Lease Contract fields the wizard may set (parties, ejar, financial…).
_WIZARD_FIELDS = (
	"contract_subtype", "ejar_contract_no", "tenant_vat_number", "billing_cycle",
	"start_date", "end_date", "hijri_start_date", "hijri_end_date", "sealing_date",
	"payment_day", "retainer_fee", "security_deposit_extra", "payment_methods_text",
	"brokerage_fee", "general_services_amount", "waste_removal_fee",
	"engineering_supervision_fee", "unit_finishing_fee",
	"electricity_annual", "water_annual", "gas_annual", "parking_annual", "parking_lots_rented",
	"lessor_org_type", "lessor_company_name", "lessor_cr_number", "lessor_unified_number", "lessor_vat_number",
	"tenant_org_type", "tenant_company_name", "tenant_cr_number", "tenant_unified_number",
	"broker_company_name", "broker_cr_number", "broker_employee_name",
	"deed_number", "deed_type", "deed_issuer", "deed_issue_date",
	"business_name", "business_cr_number", "isic_activity", "license_number",
	"lessor_obligations", "tenant_obligations", "additional_terms",
	"guarantor_name", "guarantor_id_number", "guarantor_phone",
)


@frappe.whitelist()
def available_units():
	"""Vacant units (with their property) in the companies the caller may see —
	feeds the wizard's multi-unit picker."""
	companies = frappe.get_list("Company", pluck="name") or []
	if not companies:
		return []
	comp = tuple(companies) if len(companies) > 1 else (companies[0], companies[0])
	return frappe.db.sql(
		"""
		SELECT reu.name AS unit, reu.unit_number, reu.property, p.property_name,
		       COALESCE(reu.market_rent, 0) AS market_rent, COALESCE(reu.deposit_amount, 0) AS deposit_amount
		FROM `tabReal Estate Unit` reu
		JOIN `tabProperty` p ON p.name = reu.property
		WHERE p.company IN %(comp)s AND (reu.status IS NULL OR reu.status = 'Vacant')
		ORDER BY p.property_name, reu.unit_number
		""",
		{"comp": comp},
		as_dict=True,
	)


def _get_or_create_customer(name, phone=None):
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Tenant name is required."))
	phone = (phone or "").strip()
	# Reuse an existing party ONLY on a UNIQUE mobile match — never silently bind by
	# the non-unique display name (that could attach the lease + its invoices to an
	# unrelated same-named customer's ledger). Otherwise create a fresh party.
	if phone:
		matches = frappe.get_all("Customer", filters={"mobile_no": phone}, pluck="name")
		if len(matches) == 1:
			return matches[0]
	cust = frappe.new_doc("Customer")
	cust.customer_name = name
	cust.customer_type = "Individual"
	if phone:
		cust.mobile_no = phone
	# Fill required defaults so creation works on any site.
	cust.customer_group = (
		frappe.db.get_single_value("Selling Settings", "customer_group")
		or frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	)
	cust.territory = (
		frappe.db.get_single_value("Selling Settings", "territory")
		or frappe.db.get_value("Territory", {"is_group": 0}, "name")
	)
	cust.flags.ignore_permissions = True
	cust.insert()
	return cust.name


@frappe.whitelist()
def create_lease_from_wizard(data):
	"""Create (and optionally activate) a Lease Contract + its units from the wizard.
	One request = one transaction. Native validation (commercial VAT, unit overlap)
	still runs on insert/submit."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	import json

	from frappe.utils import cint

	payload = json.loads(data) if isinstance(data, str) else (data or {})
	c = payload.get("contract") or {}
	units = payload.get("units") or []
	publish = cint(payload.get("publish"))

	if not units:
		frappe.throw(_("Add at least one unit to the contract."))

	unit0 = units[0].get("unit")
	prop = frappe.db.get_value("Real Estate Unit", unit0, "property")
	if not prop:
		frappe.throw(_("Selected unit not found."))
	company = frappe.db.get_value("Property", prop, "company")

	# Company boundary: the client-supplied unit ids are NOT covered by Frappe's
	# Company user-permission (Real Estate Unit is company-linked only via Property),
	# so re-apply the same scope available_units() uses — never trust the raw ids.
	allowed = set(frappe.get_list("Company", pluck="name") or [])
	if company not in allowed:
		frappe.throw(_("Not permitted for this company."), frappe.PermissionError)

	lease = frappe.new_doc("Lease Contract")
	lease.customer = _get_or_create_customer(c.get("tenant_name"), c.get("tenant_phone"))
	lease.property = prop
	lease.company = company
	lease.contract_type = c.get("contract_type") if c.get("contract_type") in ("Residential", "Commercial") else "Residential"
	for f in _WIZARD_FIELDS:
		if c.get(f) not in (None, ""):
			lease.set(f, c.get(f))

	total_deposit = 0.0
	for u in units:
		un = u.get("unit")
		# Validate EVERY unit (not just the first): it must exist, belong to the same
		# property (hence company), and be vacant — closes the cross-company/foreign
		# unit hijack where a crafted payload mixes another tenant's unit in.
		info = frappe.db.get_value("Real Estate Unit", un, ["property", "status"], as_dict=True)
		if not info:
			frappe.throw(_("Unit {0} not found.").format(un))
		if info.property != prop:
			frappe.throw(_("All units on a contract must belong to the same property."))
		if info.status and info.status != "Vacant":
			frappe.throw(_("Unit {0} is not available.").format(un))
		lease.append("units", {
			"unit": un,
			"annual_rent": flt(u.get("annual_rent")),
			"deposit_amount": flt(u.get("deposit")),
		})
		total_deposit += flt(u.get("deposit"))
	if not flt(lease.deposit_amount):
		lease.deposit_amount = total_deposit

	lease.insert()
	if publish:
		lease.submit()
	return {"lease": lease.name, "submitted": bool(publish)}
