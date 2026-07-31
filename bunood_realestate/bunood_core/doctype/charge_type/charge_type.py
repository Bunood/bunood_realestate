# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ChargeType(Document):
	def validate(self):
		self._block_rent_item_collision()

	def _block_rent_item_collision(self):
		"""Reverse mirror of the Company Profile guard: a Charge Type may never use ANY
		company's configured rent item as its Service Item — the rent item is the cash-basis
		owner-payout discriminator, and a collision would count charge cash as rent."""
		if not self.item:
			return
		try:
			from bunood_realestate.real_estate.company_settings import all_configured_values

			rent_items = all_configured_values("default_rent_item")
		except Exception:
			return  # settings tables not synced yet (fresh install) — never block a save
		if self.item in rent_items:
			frappe.throw(
				_(
					"Item {0} is configured as a Default Rent Item — a Charge Type must use its own "
					"Service Item so charge cash is never counted as rent in the owner payout."
				).format(self.item)
			)
