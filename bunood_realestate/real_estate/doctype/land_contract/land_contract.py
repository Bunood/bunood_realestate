# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class LandContract(Document):
	def validate(self):
		if self.start_date and self.end_date and getdate(self.end_date) <= getdate(self.start_date):
			frappe.throw(_("End Date must be after Start Date."))

	def on_submit(self):
		self.db_set("status", "Active")
		frappe.db.set_value("Land", self.land, "status", "Leased")

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		# Only free the land if no OTHER active contract holds it.
		other = frappe.db.exists(
			"Land Contract",
			{"land": self.land, "status": "Active", "docstatus": 1, "name": ["!=", self.name]},
		)
		if not other:
			frappe.db.set_value("Land", self.land, "status", "Available")
