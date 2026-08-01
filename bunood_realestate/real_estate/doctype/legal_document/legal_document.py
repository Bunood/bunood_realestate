# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Legal Document — the register row for one physical compliance document.

A first-class, heterogeneous register (a Dynamic Link attaches each row to a Company,
Customer, Supplier, Property, Lease Contract, or Land). The daily expiry sweep in
`real_estate/notifications.py` reads the `expiry_date` and reminds operators before it
lapses. A perpetual document (Title Deed صك, VAT registration) never carries an expiry and
is excluded from the sweep by construction — the deed is identity, not an expiring paper.
"""

import frappe
from frappe import _
from frappe.model.document import Document

# The company-bearing app entities we can derive the scoping company from.
_COMPANY_FROM_LINK = ("Property", "Lease Contract", "Land")
# Statuses that represent a still-current document (block a duplicate; renewable).
_LIVE_STATUSES = ("Active", "Renewal In Progress")
# Fields a renewal copies verbatim from the document it replaces.
_RENEW_COPY_FIELDS = ("document_type", "link_doctype", "link_name", "company", "issuer", "responsible_user")


class LegalDocument(Document):
	def validate(self):
		self._apply_perpetual()
		self._default_link_doctype()
		self._default_company()
		self._require_expiry()
		self._block_duplicate()

	def _default_link_doctype(self):
		"""Pre-select the entity type from the document type's 'Applies To' when the operator
		left it blank (e.g. a Commercial Registration defaults to Company)."""
		if self.link_doctype or not self.document_type:
			return
		applies_to = frappe.db.get_value("RE Document Type", self.document_type, "applies_to")
		if applies_to:
			self.link_doctype = applies_to

	def _apply_perpetual(self):
		"""Resolve is_perpetual AUTHORITATIVELY from the master here — never rely on the
		client/`fetch_from` having populated it (an API or data-import insert may not have, and
		fetch ordering vs validate is not guaranteed). A perpetual document can never hold an
		expiry, so it can never enter the sweep even if an operator typed a date (constraint #4)."""
		if self.document_type:
			self.is_perpetual = frappe.db.get_value("RE Document Type", self.document_type, "is_perpetual") or 0
		if self.is_perpetual:
			self.expiry_date = None
			self.hijri_expiry_date = None

	def _default_company(self):
		"""Scope every document to a company (the permission anchor). Derive it from the
		linked entity when the operator left it blank."""
		if self.company or not self.link_name:
			return
		if self.link_doctype == "Company":
			self.company = self.link_name
		elif self.link_doctype in _COMPANY_FROM_LINK:
			self.company = frappe.db.get_value(self.link_doctype, self.link_name, "company")

	def _require_expiry(self):
		"""Backs up `mandatory_depends_on` for API / import inserts that bypass the client."""
		if not self.is_perpetual and not self.expiry_date:
			frappe.throw(_("Expiry Date is required unless the document type is perpetual."))

	def _block_duplicate(self):
		"""One live register row per physical document. A renewed copy should supersede the old
		row (status), not create a second live duplicate that would double-remind. Both Active
		and Renewal-In-Progress count as live."""
		if self.status not in _LIVE_STATUSES or not self.link_name:
			return
		dupe = frappe.db.get_value(
			"Legal Document",
			{
				"link_doctype": self.link_doctype,
				"link_name": self.link_name,
				"document_type": self.document_type,
				"document_number": self.document_number or "",
				"status": ["in", _LIVE_STATUSES],
				"name": ["!=", self.name or ""],
			},
			"name",
		)
		if dupe:
			frappe.throw(
				_("A live {0} ({1}) is already registered for {2} {3} as {4}. Supersede it instead of adding a duplicate.").format(
					self.document_type, self.document_number or "-", self.link_doctype or "-", self.link_name or "-", dupe
				)
			)


@frappe.whitelist()
def renew_document(name, new_expiry_date=None, new_document_number=None, new_issue_date=None):
	"""Create the next version of a document: a fresh Active register row that `supersedes` the
	old one (forming a version chain, so every prior copy stays queryable), and flip the old row
	to Superseded. The old row is superseded FIRST so the new Active insert doesn't trip the
	duplicate guard; if the new insert fails (e.g. missing expiry) the whole request rolls back."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	# Lock the row and re-check status UNDER the lock: two concurrent renew clicks would each
	# supersede-then-insert, producing two Active successors. The loser blocks here, then sees
	# the committed 'Superseded' and stops — never a double renewal.
	locked_status = frappe.db.get_value("Legal Document", name, "status", for_update=True)
	if locked_status not in _LIVE_STATUSES:
		frappe.throw(_("Only a live document can be renewed (this one is {0}).").format(locked_status))
	old = frappe.get_doc("Legal Document", name)

	renewable, perpetual = frappe.db.get_value(
		"RE Document Type", old.document_type, ["renewable", "is_perpetual"]
	) or (0, 0)
	if perpetual or not renewable:
		frappe.throw(_("Document type {0} is not renewable.").format(old.document_type))

	# Supersede the old row first (same transaction) so the new Active row is unique.
	old.db_set("status", "Superseded")

	new = frappe.new_doc("Legal Document")
	for f in _RENEW_COPY_FIELDS:
		new.set(f, old.get(f))
	new.document_number = new_document_number or old.document_number
	new.issue_date = new_issue_date or frappe.utils.nowdate()
	new.expiry_date = new_expiry_date  # required by validate for a renewable (non-perpetual) type
	new.status = "Active"
	new.supersedes = old.name
	new.insert()
	return new.name
