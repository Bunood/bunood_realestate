# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class MaintenanceWorkOrder(Document):
	def validate(self):
		total = 0.0
		for row in self.items or []:
			row.amount = flt(row.qty) * flt(row.rate)
			total += flt(row.amount)
		self.total_cost = total

	def on_update(self):
		# Keep the parent request in step: dispatching a work order moves an Open
		# request to Assigned; completing all work orders is left to the operator.
		if self.status == "In Progress":
			self._nudge_request("In Progress")
		elif self.status == "Open":
			self._nudge_request("Assigned")

	def _nudge_request(self, target):
		if not self.maintenance_request:
			return
		current = frappe.db.get_value("Maintenance Request", self.maintenance_request, "status")
		# Only advance from the earliest states; never override a manual Resolved/Closed/Cancelled.
		advanceable = {"Open", "Assigned", "In Progress"}
		if current in advanceable and current != target:
			order = ["Open", "Assigned", "In Progress"]
			if order.index(target) > order.index(current):
				frappe.db.set_value("Maintenance Request", self.maintenance_request, "status", target)
