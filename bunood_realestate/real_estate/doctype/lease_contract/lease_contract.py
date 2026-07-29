# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, date_diff, flt, getdate, nowdate

from bunood_realestate.real_estate.gl_utils import assert_company_access, resolve_cost_center

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
		# Escalation sanity: ≤ -100% would generate zero/negative rent periods from year 2 on.
		# (A mild negative step-down stays legal — some renegotiations do reduce rent.)
		if flt(self.get("escalation_pct")) <= -100:
			frappe.throw(_("Annual Escalation % must be greater than -100."))

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

	def before_submit(self):
		self._snapshot_handover()

	def _snapshot_handover(self):
		"""Copy each leased unit's CURRENT inventory into the immutable `handover` child
		table (values, not references). Assets outlive contracts: the live inventory may
		change tomorrow, but this contract must forever show what was actually delivered.
		Runs in before_submit so the snapshot is part of the submitted document itself."""
		if self.amended_from or self.get("handover"):
			# An AMENDED copy always inherits its original snapshot (even an empty one) —
			# re-snapshotting at amend time could assert items the tenant never received.
			# Renewals/duplicates arrive here EMPTY (no_copy + renew_lease reset) and get
			# a fresh snapshot of the live inventory, which is exactly right for them.
			return
		units = [row.unit for row in (self.units or []) if row.unit]
		if not units:
			return
		items = frappe.get_all(
			"Unit Inventory Item",
			filters={"unit": ["in", units]},
			fields=["unit", "item_type", "qty", "brand", "condition"],
			order_by="unit asc, item_type asc",
		)
		for r in snapshot_rows(items):
			self.append("handover", r)

	def on_submit(self):
		from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import generate_for_lease

		from bunood_realestate.real_estate.charge_engine import seed_charges_for_lease

		self.db_set("status", "Active")
		self._set_units_status("Occupied", current_lease=self.name)
		# Generate the full planned rent schedule (due dates + prorated installments).
		generate_for_lease(self)
		# Seed the recurring-charge schedule (utilities/services) on its own independent rail.
		seed_charges_for_lease(self)
		# A submitted renewal marks its parent contract Renewed.
		if self.contract_subtype == "Renewal" and self.parent_lease:
			frappe.db.set_value("Lease Contract", self.parent_lease, "status", "Renewed")
		# Raise one-time fees as pending Charges (Bunood Core engine).
		self._raise_fee_charges()
		# Migration: carry an imported contract's outstanding as an is_opening invoice.
		self._raise_opening_balance()

	def on_cancel(self):
		from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import cancel_for_lease

		from bunood_realestate.real_estate.charge_engine import cancel_charges_for_lease

		self._block_cancel_if_invoiced()
		self.db_set("status", "Cancelled")
		self._free_units()
		cancel_for_lease(self)
		cancel_charges_for_lease(self)
		self._cancel_fee_charges()
		# Reverse the on_submit side effect on the parent: a cancelled renewal must not
		# leave the original lease stuck in "Renewed" (which would block re-renewal).
		if self.contract_subtype == "Renewal" and self.parent_lease:
			if frappe.db.get_value("Lease Contract", self.parent_lease, "status") == "Renewed":
				frappe.db.set_value("Lease Contract", self.parent_lease, "status", "Active")

	def _free_units(self):
		"""Free each unit ONLY if no OTHER Active submitted lease still holds it, and only
		clear current_lease if it points at THIS lease — so cancelling one of two
		back-to-back leases can't free a unit the other still occupies (mirror of
		Land Contract.on_cancel; keeps unit.status in step with the real leases)."""
		for row in self.units:
			if not row.unit:
				continue
			other = frappe.db.sql(
				"""
				SELECT lc.name FROM `tabLease Contract` lc
				JOIN `tabLease Unit` lu ON lu.parent = lc.name
				WHERE lc.docstatus = 1 AND lc.status = 'Active' AND lu.unit = %s AND lc.name != %s
				LIMIT 1
				""",
				(row.unit, self.name),
			)
			if other:
				frappe.db.set_value("Real Estate Unit", row.unit, "current_lease", other[0][0])
			else:
				vals = {"status": "Vacant"}
				if frappe.db.get_value("Real Estate Unit", row.unit, "current_lease") == self.name:
					vals["current_lease"] = None
				frappe.db.set_value("Real Estate Unit", row.unit, vals)

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

	def _raise_opening_balance(self):
		"""Migration (import_historical_seed): post the imported contract's carried-forward
		outstanding as an ``is_opening`` Sales Invoice — Dr Debtors / Cr Opening Balance
		account — linked to the lease and tagged with the Property dimension.

		is_opening keeps the amount OUT of current-period rent income (no revenue distortion),
		yet shows on the tenant's Statement of Account and AR aging and can be matched against a
		future payment. ``seed_future_periods`` has already stopped the past periods from
		becoming back-dated invoices, so this is the ONLY thing billed for the historical part."""
		if not self.import_historical_seed or self.opening_invoice:
			return
		amount = flt(self.import_contract_total)
		if amount <= 0:
			return
		settings = frappe.get_single("Real Estate Settings")
		if not settings.opening_balance_account:
			frappe.throw(
				_("Set an Opening Balance Account in Real Estate Settings before importing a contract with an outstanding balance.")
			)
		if not settings.default_rent_item:
			frappe.throw(_("Set a Default Rent Item in Real Estate Settings first."))

		si = frappe.new_doc("Sales Invoice")
		si.customer = self.customer
		si.company = self.company
		si.is_opening = "Yes"  # excludes it from current-period revenue; AR opening entry
		si.set_posting_time = 1
		si.posting_date = nowdate()
		si.due_date = nowdate()
		si.currency = frappe.get_cached_value("Company", self.company, "default_currency")
		si.conversion_rate = 1
		if settings.receivable_account:
			si.debit_to = settings.receivable_account
		si.property = self.property
		si.remarks = _("Opening balance for imported lease {0}").format(self.name)

		item = si.append("items", {})
		item.item_code = settings.default_rent_item
		item.qty = 1
		item.rate = amount
		item.income_account = settings.opening_balance_account  # Temporary Opening, not rent income
		item.description = _("Opening balance — imported lease {0}").format(self.name)
		cc = resolve_cost_center(self.company)
		if cc:
			item.cost_center = cc
		item.property = self.property

		si.flags.ignore_permissions = True
		si.insert()
		si.submit()  # submitted so it appears on the Statement of Account / AR aging
		self.db_set("opening_invoice", si.name)

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
		if not live:
			# A submitted recurring-charge (utility/service) invoice is real billing too.
			live = frappe.db.sql(
				"""
				SELECT si.name
				FROM `tabCharge Schedule` cs
				JOIN `tabSales Invoice` si ON si.name = cs.sales_invoice
				WHERE cs.lease_contract = %s AND si.docstatus = 1
				LIMIT 1
				""",
				self.name,
			)
		if not live and self.opening_invoice:
			# A submitted opening-balance invoice is real billing too — block cancel until it
			# is cancelled/credited (mirror of the rent/fee discipline).
			if frappe.db.get_value("Sales Invoice", self.opening_invoice, "docstatus") == 1:
				live = [[self.opening_invoice]]
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
	"""Create a Draft renewal: same units/terms, dates shifted to follow the old term.

	Rent basis: unit rows always hold the YEAR-1 base (escalation is a billing-time
	computation), so an escalated source lease is first rolled FORWARD to its final
	escalated year — the rate the tenant is actually paying — and only then bumped by
	``rent_bump_pct``. Without the roll-forward, renewing a 3-year 10%-escalated lease
	would silently reset the rent below the last-billed rate."""
	from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import (
		escalation_segments,
	)

	frappe.only_for(["Accounts Manager", "System Manager"])
	src = frappe.get_doc("Lease Contract", lease_contract)
	assert_company_access(src.company)  # record/company scope beyond the role gate

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
	esc = flt(src.get("escalation_pct"))
	if esc:
		# Roll to the source's FINAL escalated year: (1+esc)^(segments-1).
		factor *= ((100.0 + esc) / 100.0) ** (escalation_segments(src.start_date, src.end_date) - 1)
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

	# A renewal is a NEW handover moment: drop the copied snapshot so before_submit
	# re-snapshots the unit's CURRENT inventory at renewal submit (a fridge added or a
	# sofa removed mid-term must be reflected — the old contract keeps its own record).
	new.set("handover", [])

	# Enforce create-perm on the copied lease (the gated roles hold it) instead of
	# bypassing it, matching the wizard/importer creation paths.
	new.insert()
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


def snapshot_rows(inventory_items):
	"""Pure & testable: turn live Unit Inventory rows into immutable handover-snapshot rows
	(plain values — a later master rename/delete must never alter an old contract)."""
	rows = []
	for it in inventory_items or []:
		qty = int(it.get("qty") or 0)
		if qty < 1:
			continue
		rows.append({
			"item_label": it.get("item_type") or "",
			"qty": qty,
			"brand": it.get("brand") or "",
			"condition": it.get("condition") or "",
			"source_unit": it.get("unit") or "",
		})
	return rows


def _get_or_create_customer(name, phone=None):
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Tenant name is required."))
	phone = (phone or "").strip()
	# Reuse an existing party ONLY on a UNIQUE mobile match — never silently bind by
	# the non-unique display name (that could attach the lease + its invoices to an
	# unrelated same-named customer's ledger). Otherwise create a fresh party.
	if phone:
		# Reuse the existing party on ANY mobile match (oldest first) — not only a unique
		# one. Matching just the first hit still dedups, and it avoids the trap where two
		# customers already share a phone so the function would mint a NEW party forever.
		matches = frappe.get_all(
			"Customer", filters={"mobile_no": phone}, pluck="name", order_by="creation asc"
		)
		if matches:
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


def _build_lease(c, units, publish=0):
	"""Shared builder used by BOTH the wizard and the Excel importer (no duplication).
	Validates units (same property/company, vacant), get-or-creates the tenant, inserts,
	and optionally submits. The caller wraps each call in its own transaction."""
	if not units:
		frappe.throw(_("Add at least one unit to the contract."))

	unit0 = units[0].get("unit")
	prop = frappe.db.get_value("Real Estate Unit", unit0, "property")
	if not prop:
		frappe.throw(_("Unit {0} not found.").format(unit0))
	company = frappe.db.get_value("Property", prop, "company")

	# Company boundary: the client-supplied unit ids are NOT covered by Frappe's
	# Company user-permission (Real Estate Unit is company-linked only via Property),
	# so re-apply the same scope available_units() uses — never trust the raw ids.
	allowed = set(frappe.get_list("Company", pluck="name") or [])
	if company not in allowed:
		frappe.throw(_("Not permitted for this company."), frappe.PermissionError)

	lease = frappe.new_doc("Lease Contract")
	# Wizard autocomplete: an explicitly PICKED existing Customer wins (referential
	# integrity — no risk of minting a near-duplicate); free-typed text keeps the
	# fast-onboarding dedupe/mint path.
	if c.get("customer") and frappe.db.exists("Customer", c.get("customer")):
		lease.customer = c.get("customer")
	else:
		lease.customer = _get_or_create_customer(c.get("tenant_name"), c.get("tenant_phone"))
	lease.property = prop
	lease.company = company
	lease.contract_type = c.get("contract_type") if c.get("contract_type") in ("Residential", "Commercial") else "Residential"
	for f in _WIZARD_FIELDS:
		if c.get(f) not in (None, ""):
			lease.set(f, c.get(f))

	start, end = c.get("start_date"), c.get("end_date")
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
		# Duplicate guard at CREATION time: reject if any non-cancelled lease (drafts
		# included) already covers this unit for an overlapping period. Stops a re-run of
		# the Excel import or a double-submitted wizard from minting a duplicate draft
		# (the submit-time overlap guard only sees Active leases, not drafts).
		if start and end:
			dupe = frappe.db.sql(
				"""
				SELECT lc.name FROM `tabLease Contract` lc
				JOIN `tabLease Unit` lu ON lu.parent = lc.name
				WHERE lc.docstatus < 2 AND lu.unit = %s
				  AND lc.start_date <= %s AND lc.end_date >= %s
				LIMIT 1
				""",
				(un, end, start),
			)
			if dupe:
				frappe.throw(_("Unit {0} already has a contract ({1}) covering this period.").format(un, dupe[0][0]))
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


@frappe.whitelist()
def create_lease_from_wizard(data):
	"""Create (and optionally activate) a Lease Contract + its units from the wizard."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	import json

	from frappe.utils import cint

	payload = json.loads(data) if isinstance(data, str) else (data or {})
	return _build_lease(payload.get("contract") or {}, payload.get("units") or [], cint(payload.get("publish")))


LEASE_IMPORT_COLUMNS = [
	"tenant_name", "tenant_phone", "unit", "contract_type",
	"start_date", "end_date", "billing_cycle", "annual_rent", "deposit",
]


def _norm_contract_type(v):
	return "Commercial" if str(v or "").strip().lower() in ("commercial", "تجاري", "c") else "Residential"


@frappe.whitelist()
def import_leases(file_url):
	"""Bulk-create DRAFT leases from an uploaded .xlsx (header row = LEASE_IMPORT_COLUMNS).
	Each row is its own transaction — one bad row doesn't abort the batch. Reuses
	_build_lease so importer + wizard share identical validation (no bunood_core-style
	duplicate import path)."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

	rows = read_xlsx_file_from_attached_file(file_url=file_url) or []
	rows = [r for r in rows if any((str(x).strip() if x is not None else "") for x in r)]
	if len(rows) < 2:
		frappe.throw(_("The file is empty or has only a header row."))

	header = [str(h).strip().lower() for h in rows[0]]
	col = {name: header.index(name) for name in header}
	if "unit" not in col or "tenant_name" not in col:
		frappe.throw(_("Missing required columns: tenant_name and unit."))

	def cell(row, name):
		i = col.get(name)
		if i is None or i >= len(row):
			return None
		v = row[i]
		return v.strip() if isinstance(v, str) else v

	created, errors = [], []
	for n, row in enumerate(rows[1:], start=2):
		try:
			c = {
				"tenant_name": cell(row, "tenant_name"),
				"tenant_phone": cell(row, "tenant_phone"),
				"contract_type": _norm_contract_type(cell(row, "contract_type")),
				"start_date": cell(row, "start_date"),
				"end_date": cell(row, "end_date"),
				"billing_cycle": cell(row, "billing_cycle") or "Monthly",
			}
			units = [{"unit": cell(row, "unit"), "annual_rent": cell(row, "annual_rent"), "deposit": cell(row, "deposit")}]
			res = _build_lease(c, units, publish=0)
			created.append(res["lease"])
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			errors.append({"row": n, "error": str(e)[:200]})
	return {"created": created, "errors": errors}


# ------------------------------------------------------------------------------
# Lease auto-expiry (daily scheduler). Without this a lease stays Active — and its
# units stay Occupied — forever after end_date, drifting the occupancy KPI and making
# a genuinely-free unit unbookable in the wizard (which trusts unit.status). All in-app,
# no ERPNext-core change: it only flips the app's own Lease/Unit status via native writes.
# ------------------------------------------------------------------------------
def lease_is_expired(end_date, today):
	"""Pure predicate (offline-testable): an Active lease is due for auto-expiry once its
	end_date is STRICTLY before `today` — the end_date day itself is still covered."""
	if not end_date:
		return False
	return getdate(end_date) < getdate(today)


def expire_due_leases():
	"""Daily: move Active leases past their end_date to Expired and free their units.
	Idempotent + concurrency-safe: each lease is re-checked under a row lock, and only
	Active leases are touched, so a re-run (or a race with submit/terminate/renew) no-ops.
	Fail-loud per row (rollback + log) so one bad lease never blocks the rest."""
	names = frappe.get_all(
		"Lease Contract",
		filters={"docstatus": 1, "status": "Active", "end_date": ["<", getdate(nowdate())]},
		pluck="name",
	)
	expired = 0
	for name in names:
		try:
			if _expire_one_lease(name):
				expired += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: lease auto-expiry failed",
				message=f"Lease {name}\n\n{frappe.get_traceback()}",
			)
	return expired


def _expire_one_lease(name):
	"""Expire a single lease under a row lock. Returns True if it was expired."""
	# Locking read returns the latest COMMITTED status, so a lease terminated/renewed/
	# cancelled by a concurrent worker is seen here and skipped (no double transition).
	guard = frappe.db.get_value(
		"Lease Contract", name, ["status", "end_date"], for_update=True, as_dict=True
	)
	if not guard or guard.status != "Active" or not lease_is_expired(guard.end_date, nowdate()):
		return False
	doc = frappe.get_doc("Lease Contract", name)
	# Flip status first so _free_units' "any OTHER Active lease still holds this unit?" query
	# no longer counts this lease, then release units not held by another active lease.
	doc.db_set("status", "Expired")
	doc._free_units()
	return True
