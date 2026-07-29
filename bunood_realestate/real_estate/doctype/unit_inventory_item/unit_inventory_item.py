# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class UnitInventoryItem(Document):
	def validate(self):
		if (self.qty or 0) < 1:
			frappe.throw(_("Qty must be at least 1."))
