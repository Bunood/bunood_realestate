# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from bunood_realestate.real_estate.gl_utils import assert_company_access, resolve_cost_center


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


@frappe.whitelist()
def post_contractor_bill(work_order):
	"""Turn a completed work order's cost into an ERPNext Purchase Invoice to the contractor,
	so maintenance spend actually hits the GL and per-property P&L (previously it never did —
	`total_cost` was computed but never posted). Explicit action (button), matching the app's
	"GL only via a deliberate step" pattern — never coupled to a save. All native docs, no
	ERPNext-core change. Idempotent + concurrency-safe via a row lock on the work order."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	doc = frappe.get_doc("Maintenance Work Order", work_order)
	# ignore_permissions post below → verify the caller may act in this company first.
	assert_company_access(doc.company)

	if doc.status != "Done":
		frappe.throw(_("Post the contractor bill only when the work order is Done."))
	if not doc.contractor:
		frappe.throw(_("Set the Contractor (Supplier) on the work order first."))
	if flt(doc.total_cost) <= 0:
		frappe.throw(_("Work order total cost is zero — nothing to bill."))

	# Lock the work order row; a locking read returns the latest COMMITTED purchase_invoice,
	# so a concurrent click/second poster sees our committed link and stops (no double-bill).
	locked = frappe.db.get_value("Maintenance Work Order", doc.name, "purchase_invoice", for_update=True)
	if locked:
		return {"purchase_invoice": locked, "already": True}

	from bunood_realestate.real_estate.company_settings import require_company_config

	settings = require_company_config(
		doc.company, ["maintenance_item", "maintenance_expense_account"]
	)
	if not settings.maintenance_item:
		frappe.throw(_("Set a Maintenance Item in Real Estate Settings before billing a work order."))
	# Set the expense head EXPLICITLY (like every other GL path in the app) rather than
	# relying on ERPNext's implicit item/item-group default, which is commonly unset on a
	# Saudi site without perpetual inventory and would raise "Expense account is mandatory".
	if not settings.maintenance_expense_account:
		frappe.throw(_("Set a Maintenance Expense Account in Real Estate Settings before billing a work order."))

	pi = frappe.new_doc("Purchase Invoice")
	pi.supplier = doc.contractor
	pi.company = doc.company
	pi.currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	pi.conversion_rate = 1
	pi.set_posting_time = 1
	pi.posting_date = doc.scheduled_date or nowdate()
	pi.bill_no = f"MWO-{doc.name}"
	pi.bill_date = pi.posting_date

	item = pi.append("items", {})
	item.item_code = settings.maintenance_item
	item.qty = 1
	item.rate = flt(doc.total_cost)
	item.expense_account = settings.maintenance_expense_account
	item.description = _("Maintenance {0}").format(doc.maintenance_request or doc.name)
	# P&L expense line → company-matching cost center (the app's cost-center discipline).
	cc = resolve_cost_center(doc.company)
	if cc:
		item.cost_center = cc
	# Tag with Property/Unit accounting dimensions so the maintenance expense nets into that
	# property's P&L (mirrors head_lease / rent invoicing).
	if doc.property:
		item.property = doc.property
		pi.property = doc.property
	if doc.unit:
		item.real_estate_unit = doc.unit

	pi.flags.ignore_permissions = True
	pi.insert()
	pi.submit()

	doc.db_set("purchase_invoice", pi.name)
	return {"purchase_invoice": pi.name, "already": False}
