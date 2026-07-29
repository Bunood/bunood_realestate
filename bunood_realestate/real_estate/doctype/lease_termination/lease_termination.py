# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate

from bunood_realestate.real_estate.gl_utils import require_cost_center


def unused_rent_credit(period_start, period_end, base_amount, termination_date):
	"""Pure & testable: the ex-VAT amount to credit back for a rent period when the lease
	terminates on ``termination_date``. The tenant owes rent up to AND INCLUDING the
	termination date; days after it are unused.

	  - termination before the period starts  → full period credited
	  - termination on/after the period end   → nothing to credit
	  - termination inside the period         → prorated by unused days / total days
	"""
	ps, pe, td = getdate(period_start), getdate(period_end), getdate(termination_date)
	total_days = date_diff(pe, ps) + 1
	if total_days <= 0:
		return 0.0
	if td < ps:
		unused = total_days
	elif td >= pe:
		return 0.0
	else:
		unused = date_diff(pe, td)  # days td+1 .. pe inclusive
	return flt(flt(base_amount) * unused / total_days, 2)


class LeaseTermination(Document):
	def validate(self):
		lease = frappe.get_doc("Lease Contract", self.lease_contract)
		# Only an Active lease can be terminated; a second live termination (or a typo'd
		# date outside the term) would post wrong revenue reversals as submitted GL docs.
		if lease.status != "Active":
			frappe.throw(_("Only an Active lease can be terminated (this lease is {0}).").format(lease.status))
		other = frappe.db.get_value(
			"Lease Termination",
			{"lease_contract": self.lease_contract, "docstatus": 1, "name": ["!=", self.name]},
			"name",
		)
		if other:
			frappe.throw(_("Lease Termination {0} already exists for this lease.").format(other))
		if self.termination_date and not (
			getdate(lease.start_date) <= getdate(self.termination_date) <= getdate(lease.end_date)
		):
			frappe.throw(
				_("Termination Date must fall inside the lease term ({0} to {1}).").format(
					lease.start_date, lease.end_date
				)
			)
		self.deposit_held = flt(lease.deposit_received) - flt(lease.deposit_refunded)
		self.total_deductions = sum(flt(d.amount) for d in self.deductions)
		self.net_refund = flt(self.deposit_held) - flt(self.total_deductions)

		if self.net_refund < 0:
			frappe.throw(_("Total deductions exceed the held deposit. Handle the excess as a separate charge."))
		if flt(self.net_refund) > 0 and not self.refund_account:
			frappe.throw(_("Set the Refund account (net refund is {0}).").format(self.net_refund))
		for d in self.deductions:
			if not d.income_account:
				frappe.throw(_("Each deduction needs an Income Account."))

	def on_submit(self):
		from bunood_realestate.real_estate.charge_engine import cancel_future_charges

		self._block_if_draft_invoices()
		self._cancel_future_rent()
		cancel_future_charges(self.lease_contract, self.termination_date)
		self._credit_unused_rent()
		self._post_settlement()
		self._close_lease()
		self.db_set("status", "Settled")

	def _block_if_draft_invoices(self):
		"""With auto-submit off, a period can be 'Invoiced' while its Sales Invoice is still a
		DRAFT — neither creditable nor cancellable by this termination. Fail loud and list
		them: the operator submits or deletes those drafts first, then terminates."""
		drafts = frappe.db.sql(
			"""
			SELECT si.name
			FROM `tabRent Schedule` rs
			JOIN `tabSales Invoice` si ON si.name = rs.sales_invoice
			WHERE rs.lease_contract = %s AND rs.period_end > %s AND si.docstatus = 0
			""",
			(self.lease_contract, self.termination_date),
			pluck="name",
		)
		if drafts:
			frappe.throw(
				_("Draft rent invoice(s) {0} cover periods affected by this termination. Submit or delete them first.").format(
					", ".join(drafts)
				)
			)

	def on_cancel(self):
		self._cancel_credit_notes()
		if self.refund_journal_entry:
			je = frappe.get_doc("Journal Entry", self.refund_journal_entry)
			if je.docstatus == 1:
				je.cancel()
		lease = frappe.get_doc("Lease Contract", self.lease_contract)
		lease.db_set("deposit_refunded", flt(lease.deposit_refunded) - flt(self.deposit_held))
		lease.db_set("status", "Active")
		# Re-occupy the units (symmetric to _close_lease) — otherwise the lease is Active
		# again while its units read Vacant, giving a wrong occupancy KPI and re-offering
		# a still-leased unit in the wizard.
		for row in lease.units:
			if row.unit:
				frappe.db.set_value(
					"Real Estate Unit", row.unit, {"status": "Occupied", "current_lease": lease.name}
				)
		self._restore_future_rent()
		self._restore_charges()
		self.db_set("status", "Cancelled")

	def _restore_charges(self):
		"""Reactivating the lease: re-open the charge rows this termination had cancelled."""
		from bunood_realestate.real_estate.charge_engine import restore_future_charges

		restore_future_charges(self.lease_contract, self.termination_date)

	def _restore_future_rent(self):
		"""Reactivating the lease: re-plan the rent rows this termination had cancelled."""
		rows = frappe.get_all(
			"Rent Schedule",
			filters={
				"lease_contract": self.lease_contract,
				"status": "Cancelled",
				"sales_invoice": ["in", [None, ""]],
				"due_date": [">=", self.termination_date],
			},
			pluck="name",
		)
		for name in rows:
			frappe.db.set_value("Rent Schedule", name, "status", "Planned")

	def _cancel_future_rent(self):
		"""Cancel still-Planned schedule rows due on/after the termination date."""
		rows = frappe.get_all(
			"Rent Schedule",
			filters={
				"lease_contract": self.lease_contract,
				"status": "Planned",
				"due_date": [">=", self.termination_date],
			},
			pluck="name",
		)
		for name in rows:
			frappe.db.set_value("Rent Schedule", name, "status", "Cancelled")

	def _credit_unused_rent(self):
		"""Raise a Credit Note for every INVOICED rent period extending past the termination
		date (mid-period → prorated by unused days; fully-future but already invoiced via
		lead-days → full). Standalone credit notes (is_return, no return_against) so no
		return-item/rate validations fight the proration; the AR credit offsets the tenant's
		balance natively. Idempotent: a schedule row already credited (any live credit row)
		is skipped, so a second termination against the same lease can never double-credit."""
		from bunood_realestate.real_estate.tasks import split_amount

		rows = frappe.get_all(
			"Rent Schedule",
			filters={
				"lease_contract": self.lease_contract,
				"status": "Invoiced",
				"sales_invoice": ["is", "set"],
				"period_end": [">", self.termination_date],
			},
			fields=["name", "period_start", "period_end", "base_amount", "sales_invoice"],
		)
		for r in rows:
			credit = unused_rent_credit(r.period_start, r.period_end, r.base_amount, self.termination_date)
			if credit <= 0:
				continue
			# Cross-termination idempotency: never credit a period twice.
			already = frappe.db.sql(
				"""
				SELECT ltc.name
				FROM `tabLease Termination Credit` ltc
				JOIN `tabSales Invoice` cn ON cn.name = ltc.credit_note
				WHERE ltc.rent_schedule = %s AND cn.docstatus = 1
				LIMIT 1
				""",
				r.name,
			)
			if already:
				continue
			si = frappe.get_doc("Sales Invoice", r.sales_invoice)
			if si.docstatus != 1:
				continue
			cn = self._make_credit_note(si, r, credit, split_amount)
			frappe.get_doc({
				"doctype": "Lease Termination Credit",
				"parent": self.name, "parenttype": "Lease Termination", "parentfield": "credits",
				"rent_schedule": r.name, "sales_invoice": si.name,
				"credit_note": cn.name, "amount": credit,
			}).insert(ignore_permissions=True)

	def _make_credit_note(self, si, row, credit_total, split_amount):
		"""One standalone Credit Note mirroring the original rent invoice's lines
		(item/account/cost-center/dimensions), scaled to the unused amount; the original
		tax template reapplies so VAT reverses proportionally."""
		weights = [flt(it.amount) for it in si.items]
		shares = split_amount(credit_total, weights)
		cn = frappe.new_doc("Sales Invoice")
		cn.customer = si.customer
		cn.company = si.company
		cn.currency = si.currency
		cn.conversion_rate = si.conversion_rate or 1
		cn.set_posting_time = 1
		# Never post the reversal BEFORE the revenue it reverses exists in the GL (a
		# lead-days invoice for a fully-future period posts at period_start).
		cn.posting_date = max(getdate(self.termination_date), getdate(si.posting_date))
		cn.is_return = 1
		# Parent-level Property dimension: ERPNext stamps the receivable + tax GL rows from
		# the PARENT doc, so mirror the original invoice or those GL rows lose the dimension.
		cn.property = si.get("property")
		if si.debit_to:
			cn.debit_to = si.debit_to
		cn.remarks = _("Credit for unused rent ({0} to {1}) — lease {2} terminated {3}; against {4}").format(
			row.period_start, row.period_end, self.lease_contract, self.termination_date, si.name
		)
		for it, share in zip(si.items, shares):
			if flt(share) <= 0:
				continue
			line = cn.append("items", {})
			line.item_code = it.item_code
			line.qty = -1
			line.rate = flt(share)
			line.income_account = it.income_account
			if it.cost_center:
				line.cost_center = it.cost_center
			line.property = it.get("property")
			line.real_estate_unit = it.get("real_estate_unit")
			line.description = _("Unused rent {0} to {1}").format(self.termination_date, row.period_end)
		# Copy the ORIGINAL invoice's SAVED tax rows — not a fresh template fetch. The
		# template may have changed since invoicing; the credit must reverse the VAT that
		# was actually charged. 'Actual'-type rows scale by the credited share of the net.
		if si.taxes:
			cn.taxes_and_charges = si.taxes_and_charges
			ratio = flt(credit_total) / flt(si.base_net_total) if flt(si.base_net_total) else 0
			for t in si.taxes:
				tax = cn.append("taxes", {})
				tax.charge_type = t.charge_type
				tax.account_head = t.account_head
				tax.rate = t.rate
				tax.description = t.description
				if t.get("cost_center"):
					tax.cost_center = t.cost_center
				tax.included_in_print_rate = t.included_in_print_rate
				if t.charge_type == "Actual":
					tax.tax_amount = flt(flt(t.tax_amount) * ratio, 2)
		cn.flags.ignore_permissions = True
		cn.insert()
		cn.submit()
		return cn

	def _cancel_credit_notes(self):
		"""Symmetric undo: cancelling the termination cancels the credit notes it raised
		(the rent stands again for the reactivated lease)."""
		for c in self.credits or []:
			if not c.credit_note:
				continue
			cn = frappe.get_doc("Sales Invoice", c.credit_note)
			if cn.docstatus == 1:
				cn.cancel()

	def _post_settlement(self):
		"""DR deposit liability (held) / CR refund (net) / CR each deduction income."""
		if flt(self.deposit_held) <= 0:
			return
		settings = frappe.get_single("Real Estate Settings")
		if not settings.tenant_deposit_account:
			frappe.throw(_("Set the Tenant Security Deposit Account in Real Estate Settings."))

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = self.termination_date
		je.user_remark = _("Deposit settlement — Lease {0}").format(self.lease_contract)
		je.append("accounts", {
			"account": settings.tenant_deposit_account,
			"debit_in_account_currency": flt(self.deposit_held),
		})
		if flt(self.net_refund) > 0:
			je.append("accounts", {
				"account": self.refund_account,
				"credit_in_account_currency": flt(self.net_refund),
			})
		deduction_cc = None
		for d in self.deductions:
			if flt(d.amount) > 0:
				# Deduction accounts are income (P&L) → a company-matching cost center is
				# required for GL; resolve it once, lazily (only if there are deductions).
				if deduction_cc is None:
					deduction_cc = require_cost_center(self.company)
				je.append("accounts", {
					"account": d.income_account,
					"credit_in_account_currency": flt(d.amount),
					"cost_center": deduction_cc,
				})
		je.flags.ignore_permissions = True
		je.insert()
		je.submit()
		self.db_set("refund_journal_entry", je.name)

	def _close_lease(self):
		lease = frappe.get_doc("Lease Contract", self.lease_contract)
		# The whole held deposit is now settled (net refunded + deductions recognised).
		lease.db_set("deposit_refunded", flt(lease.deposit_refunded) + flt(self.deposit_held))
		lease.db_set("status", "Expired")
		for row in lease.units:
			if row.unit:
				frappe.db.set_value(
					"Real Estate Unit", row.unit, {"status": "Vacant", "current_lease": None}
				)


_AREA_TO_KIND = {"Keys": "Key Replacement", "Meters": "Unpaid Utilities"}


@frappe.whitelist()
def pull_inspection_charges(lease_termination):
	"""Copy each move-out inspection line that carries a suggested charge into the
	deductions — the SINGLE deduction path that drives the settlement JE (no parallel
	posting). Idempotent: each line is pulled at most once (its `pulled` flag), so a
	re-run never double-charges. The deduction income account comes from settings."""
	doc = frappe.get_doc("Lease Termination", lease_termination)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft termination can be edited."))

	pending = [r for r in doc.inspection if flt(r.charge) > 0 and not r.pulled]
	if not pending:
		return {"added": 0}

	income_account = frappe.db.get_single_value("Real Estate Settings", "deduction_income_account")
	if not income_account:
		frappe.throw(_("Set the Deposit Deduction Income Account in Real Estate Settings first."))

	added = 0
	for row in pending:
		doc.append("deductions", {
			"kind": _AREA_TO_KIND.get(row.area, "Damage"),
			"amount": flt(row.charge),
			"income_account": income_account,
		})
		row.pulled = 1
		added += 1
	doc.save()
	return {"added": added}
