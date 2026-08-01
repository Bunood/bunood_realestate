# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


def _is_nonneg_int(token):
	"""True only for tokens int() actually accepts as a non-negative integer. str.isdigit()
	alone is unsafe — it passes strings int() rejects (e.g. '--5', superscript '²')."""
	try:
		return int(token) >= 0
	except (TypeError, ValueError):
		return False


class REDocumentType(Document):
	def validate(self):
		# A perpetual document never expires: it has no reminder cadence, grace, or renewal.
		if self.is_perpetual:
			self.reminder_days = None
			self.grace_days = 0
			self.renewable = 0
			return
		# Reminder Days, if given, must contain at least one non-negative day count — else the
		# type would silently lose its cadence and fall back to the default without the operator
		# knowing. (Blank is fine: blank means "use the system default".)
		if self.reminder_days and str(self.reminder_days).strip():
			parts = [p for p in str(self.reminder_days).replace(" ", "").split(",") if p]
			if parts and not any(_is_nonneg_int(p) for p in parts):
				frappe.throw(_("Reminder Days must be a comma-separated list of non-negative day counts (e.g. '90,30,7,0')."))
		if self.grace_days and int(self.grace_days) < 0:
			frappe.throw(_("Grace Period cannot be negative."))
