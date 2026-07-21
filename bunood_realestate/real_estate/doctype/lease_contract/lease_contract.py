# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, date_diff, flt, getdate

# ZATCA VAT number: 15 digits, starts and ends with 3 (bunood_core parity).
ZATCA_VAT_RE = re.compile(r"^3\d{13}3$")


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

	def on_cancel(self):
		from bunood_realestate.real_estate.doctype.rent_schedule.rent_schedule import cancel_for_lease

		self._block_cancel_if_invoiced()
		self.db_set("status", "Cancelled")
		self._set_units_status("Vacant", current_lease=None)
		cancel_for_lease(self)

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
