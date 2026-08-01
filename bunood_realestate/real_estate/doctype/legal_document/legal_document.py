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


class LegalDocument(Document):
	def validate(self):
		self._apply_perpetual()
		self._default_company()
		self._require_expiry()
		self._block_duplicate()

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
		"""One active register row per physical document. A renewed copy should supersede the
		old row (status), not create a second Active duplicate that would double-remind."""
		if self.status != "Active" or not self.link_name:
			return
		dupe = frappe.db.get_value(
			"Legal Document",
			{
				"link_doctype": self.link_doctype,
				"link_name": self.link_name,
				"document_type": self.document_type,
				"document_number": self.document_number or "",
				"status": "Active",
				"name": ["!=", self.name or ""],
			},
			"name",
		)
		if dupe:
			frappe.throw(
				_("An active {0} ({1}) is already registered for {2} {3} as {4}. Supersede it instead of adding a duplicate.").format(
					self.document_type, self.document_number or "-", self.link_doctype or "-", self.link_name or "-", dupe
				)
			)
