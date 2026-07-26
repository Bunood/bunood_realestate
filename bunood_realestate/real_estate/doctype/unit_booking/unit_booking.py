# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate, nowdate

from bunood_realestate.real_estate.gl_utils import assert_company_access


class UnitBooking(Document):
	def validate(self):
		if not self.booking_date:
			self.booking_date = nowdate()
		if not self.company and self.property:
			self.company = frappe.db.get_value("Property", self.property, "company")
		if self.expiry_date and getdate(self.expiry_date) < getdate(self.booking_date):
			frappe.throw(_("Hold-until date cannot be before the booking date."))

	def on_submit(self):
		# Lock the unit row, then refuse a second live reservation on it or a non-vacant
		# unit — no two Reserved bookings (and no two draft leases) for one physical unit.
		frappe.db.get_value("Real Estate Unit", self.unit, "name", for_update=True)
		other = frappe.db.exists(
			"Unit Booking",
			{"unit": self.unit, "status": "Reserved", "docstatus": 1, "name": ["!=", self.name]},
		)
		if other:
			frappe.throw(_("Unit {0} already has an active reservation ({1}).").format(self.unit, other))
		unit_status = frappe.db.get_value("Real Estate Unit", self.unit, "status")
		if unit_status and unit_status != "Vacant":
			frappe.throw(_("Unit {0} is not available (status: {1}).").format(self.unit, unit_status))
		self.db_set("status", "Reserved")
		frappe.db.set_value("Real Estate Unit", self.unit, "status", "Reserved")

	def on_cancel(self):
		if self.status == "Converted":
			frappe.throw(_("A converted booking cannot be cancelled — cancel its lease instead."))
		self.db_set("status", "Cancelled")
		if frappe.db.get_value("Real Estate Unit", self.unit, "status") == "Reserved":
			frappe.db.set_value("Real Estate Unit", self.unit, "status", "Vacant")


def expire_bookings():
	"""Daily: a Reserved booking past its hold-until date becomes Expired, and its unit is
	freed if still Reserved by it — so an abandoned hold never leaves a unit stuck Reserved."""
	rows = frappe.get_all(
		"Unit Booking",
		filters={"status": "Reserved", "docstatus": 1, "expiry_date": ["<", nowdate()]},
		fields=["name", "unit"],
	)
	expired = 0
	for b in rows:
		# Per-row isolation + commit + log (match the rent/head-lease jobs): one bad
		# booking must not abort the whole daily sweep.
		try:
			frappe.db.set_value("Unit Booking", b.name, "status", "Expired")
			if b.unit and frappe.db.get_value("Real Estate Unit", b.unit, "status") == "Reserved":
				frappe.db.set_value("Real Estate Unit", b.unit, "status", "Vacant")
			frappe.db.commit()
			expired += 1
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: booking expiry failed",
				message=f"Unit Booking {b.name}\n\n{frappe.get_traceback()}",
			)
	return expired


@frappe.whitelist()
def convert_to_lease(booking):
	"""Turn an active reservation into a Draft Lease Contract (the operator then
	completes/activates it). The unit stays Reserved until the lease is submitted."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	b = frappe.get_doc("Unit Booking", booking)
	assert_company_access(b.company)  # record/company scope beyond the role gate
	if b.docstatus != 1 or b.status != "Reserved":
		frappe.throw(_("Only an active reservation can be converted."))

	lease = frappe.new_doc("Lease Contract")
	lease.customer = b.customer
	lease.property = b.property
	lease.company = b.company
	lease.contract_type = "Residential"
	lease.start_date = nowdate()
	lease.end_date = add_months(nowdate(), 12)
	lease.billing_cycle = "Monthly"
	lease.append("units", {"unit": b.unit, "annual_rent": flt(b.annual_rent), "deposit_amount": flt(b.deposit_amount)})
	lease.deposit_amount = flt(b.deposit_amount)
	lease.insert()

	b.db_set("status", "Converted")
	b.db_set("lease_contract", lease.name)
	return {"lease": lease.name}
