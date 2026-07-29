# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Meter Reading — an audit-grade reading for a metered Lease Charge. On submit it fills the
matching Awaiting-Reading Charge Schedule period (consumption × tariff) and advances the charge's
rolling previous_reading; on cancel it reverts that (if not yet invoiced). The billing logic lives
in charge_engine so it stays testable and next to the rest of the engine."""

import frappe
from frappe.model.document import Document

from bunood_realestate.real_estate.charge_engine import capture_meter_reading, revert_meter_reading


class MeterReading(Document):
	def validate(self):
		# Denormalize context from the lease + the metered Lease Charge row (all read-only).
		if self.lease_contract:
			lease = frappe.db.get_value(
				"Lease Contract", self.lease_contract, ["company", "property"], as_dict=True
			)
			if lease:
				self.company = lease.company
				self.property = self.property or lease.property
		if self.lease_charge_row and not self.unit:
			self.unit = frappe.db.get_value("Lease Charge", self.lease_charge_row, "unit")
		if self.lease_charge_row and not self.meter_no:
			self.meter_no = frappe.db.get_value("Lease Charge", self.lease_charge_row, "meter_no")

	def on_submit(self):
		capture_meter_reading(self)

	def on_cancel(self):
		revert_meter_reading(self)
