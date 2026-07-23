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
		self.db_set("status", "Reserved")
		# Reserve the unit only if it is currently free.
		if frappe.db.get_value("Real Estate Unit", self.unit, "status") == "Vacant":
			frappe.db.set_value("Real Estate Unit", self.unit, "status", "Reserved")

	def on_cancel(self):
		if self.status == "Converted":
			frappe.throw(_("A converted booking cannot be cancelled — cancel its lease instead."))
		self.db_set("status", "Cancelled")
		if frappe.db.get_value("Real Estate Unit", self.unit, "status") == "Reserved":
			frappe.db.set_value("Real Estate Unit", self.unit, "status", "Vacant")


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
