# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
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


def append_update(doc, message, photo=None, from_portal=0):
	"""Append one conversation entry (message and/or photo) to a Maintenance Request,
	stamped with the author and time. Shared by the desk action and the tenant portal.
	The CALLER is responsible for the access check (record write-perm for the desk,
	tenant-ownership scoping for the portal)."""
	message = (message or "").strip()
	photo = (photo or "").strip() or None
	if not message and not photo:
		frappe.throw(_("Write a message or attach a photo."))
	doc.append("updates", {
		"posted_on": now_datetime(),
		"author": frappe.session.user,
		"author_name": frappe.utils.get_fullname(frappe.session.user),
		"from_portal": 1 if from_portal else 0,
		"message": message[:2000] if message else None,
		"photo": photo,
	})
	# Portal path is pre-scoped to the tenant's own request but the user holds no desk
	# write-perm, so save with ignore_permissions there; the desk path checked perms.
	doc.save(ignore_permissions=bool(from_portal))
	doc.notify_update()
	return doc.updates[-1]


@frappe.whitelist()
def add_update(request, message=None, photo=None):
	"""Desk action: post a conversation update to a Maintenance Request.
	Requires record-level write permission on THIS request."""
	doc = frappe.get_doc("Maintenance Request", request)
	doc.check_permission("write")
	row = append_update(doc, message, photo, from_portal=0)
	return {"name": doc.name, "idx": row.idx}
