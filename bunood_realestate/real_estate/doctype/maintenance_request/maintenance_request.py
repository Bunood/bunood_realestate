# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class MaintenanceRequest(Document):
	def validate(self):
		if not self.reported_on:
			self.reported_on = now_datetime()
		# Default the priority from the chosen category (only when left blank).
		if self.category and not self.priority:
			self.priority = frappe.db.get_value("RE Maintenance Category", self.category, "default_priority") or "Medium"
		# Stamp / clear the resolution timestamp as the status crosses the "done" line.
		if self.status in ("Resolved", "Closed"):
			if not self.resolved_on:
				self.resolved_on = now_datetime()
		else:
			self.resolved_on = None
