# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class OwnerPayout(Document):
	"""An Owner Payout row is the idempotency key for `management.generate_owner_payout`:
	a Posted row whose (property, from_date, to_date) window overlaps blocks a re-post so
	the owner is never paid twice. That guard is only sound while the row and its backing
	Journal Entry stay in lock-step with the GL — so guard the two ways they could diverge:
	deletion (below) and JE cancellation (`events.reconcile_owner_payout_on_je`)."""

	def on_trash(self):
		"""Block deleting a Posted payout while its Journal Entry is still submitted.

		Otherwise: delete the row → the JE lives on in the GL (owner still credited) → the
		overlap guard no longer sees a Posted payout → a re-run of generate_owner_payout
		posts a SECOND payout JE for the same period → the owner is paid twice. Force the
		operator to cancel the JE first; that reverses the GL and (via the JE doc-event)
		flips this row to Cancelled, after which deletion is harmless."""
		if self.status == "Posted" and self.journal_entry:
			if frappe.db.get_value("Journal Entry", self.journal_entry, "docstatus") == 1:
				frappe.throw(
					_(
						"Cannot delete a Posted owner payout while its Journal Entry {0} is still "
						"submitted — the owner would be paid twice on the next run. Cancel the "
						"Journal Entry first (that reverses the GL and marks this payout Cancelled)."
					).format(self.journal_entry)
				)
