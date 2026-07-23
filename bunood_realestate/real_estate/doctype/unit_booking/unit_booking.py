# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate, nowdate


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
	for b in rows:
		frappe.db.set_value("Unit Booking", b.name, "status", "Expired")
		if b.unit and frappe.db.get_value("Real Estate Unit", b.unit, "status") == "Reserved":
			frappe.db.set_value("Real Estate Unit", b.unit, "status", "Vacant")
	return len(rows)


@frappe.whitelist()
def convert_to_lease(booking):
	"""Turn an active reservation into a Draft Lease Contract (the operator then
	completes/activates it). The unit stays Reserved until the lease is submitted."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	b = frappe.get_doc("Unit Booking", booking)
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
