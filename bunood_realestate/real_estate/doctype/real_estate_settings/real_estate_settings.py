# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class RealEstateSettings(Document):
	"""Single doctype holding the accounting defaults used when the rent-invoice
	generator creates Sales Invoices. Config-over-code: no account is hardcoded."""

	pass
