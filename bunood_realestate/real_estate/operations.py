# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Operations Center — the lease's installment journey, and the actions that move it
(docs/plan-invoicing-automation.md §2/§3).

Health is COMPUTED LIVE, never stored. Colours/labels are derived at read time from the
schedule's due_date and — when an invoice exists — ERPNext's own `outstanding_amount` /
`status`. The persisted `Rent Schedule.status` stays deliberately simple
(Planned / Invoiced / Cancelled / Failed); a second stored "paid" flag would be a
parallel ledger that drifts from the GL the first time someone reconciles a payment by
hand. ERPNext remains the single source of truth for money.

Actions are thin: issuance delegates to the already row-locked
`tasks._create_invoice_for_schedule`, and collection delegates to ERPNext's native
`get_payment_entry`. Nothing here re-implements accounting.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, nowdate

from bunood_realestate.real_estate import invoicing_policy

# Health keys (stable identifiers — the client maps them to colour + label + action).
PLANNED = "planned"
DUE_SOON = "due_soon"
OVERDUE_UNISSUED = "overdue_unissued"
DRAFT_INVOICE = "draft_invoice"
ISSUED = "issued"
PENDING_RECEIPT = "pending_receipt"
PARTIALLY_PAID = "partially_paid"
OVERDUE_UNPAID = "overdue_unpaid"
PAID = "paid"
CANCELLED = "cancelled"
FAILED = "failed"


def pending_receipt_amount(sales_invoice):
	"""Cash the operator has RECORDED but not yet posted — the allocated total of DRAFT
	Payment Entries against this invoice.

	This quantity has to be first-class. `receive_payment` deliberately leaves the receipt
	unsubmitted for review, and a draft PE does NOT move `Sales Invoice.outstanding_amount`
	(ERPNext only allocates on submit). Anything that decides *collection* behavior purely
	from `outstanding_amount` would therefore treat a paying tenant as a defaulter — and
	late fees post an irreversible tax invoice, so that mistake is permanent."""
	value = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(per.allocated_amount), 0)
		FROM `tabPayment Entry Reference` per
		JOIN `tabPayment Entry` pe ON pe.name = per.parent
		WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name = %s
		  AND pe.docstatus = 0
		""",
		sales_invoice,
	)
	return flt(value[0][0]) if value else 0.0


def pending_receipts_for_customer(customer, company):
	"""Same idea, aggregated for dunning: draft receipts against this tenant's invoices."""
	value = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(per.allocated_amount), 0)
		FROM `tabPayment Entry Reference` per
		JOIN `tabPayment Entry` pe ON pe.name = per.parent
		JOIN `tabSales Invoice` si ON si.name = per.reference_name
		WHERE per.reference_doctype = 'Sales Invoice'
		  AND pe.docstatus = 0 AND si.docstatus = 1
		  AND si.customer = %s AND si.company = %s
		""",
		(customer, company),
	)
	return flt(value[0][0]) if value else 0.0


def compute_health(row, today, due_soon_days):
	"""Pure & testable. ``row`` = {status, due_date, sales_invoice, invoice_docstatus,
	grand_total, outstanding}. Returns {key, days} where ``days`` is days overdue
	(positive) or days until due (negative), so the client can label precisely."""
	today = getdate(today)
	due = getdate(row.get("due_date")) if row.get("due_date") else None
	days_to_due = date_diff(due, today) if due else None

	status = row.get("status")
	if status == "Cancelled":
		return {"key": CANCELLED, "days": days_to_due}
	if status == "Failed":
		return {"key": FAILED, "days": days_to_due}

	docstatus = cint(row.get("invoice_docstatus")) if row.get("sales_invoice") else None
	if docstatus == 0:
		# A DRAFT invoice: neither a tax document nor a receivable. Legacy sites that ran
		# the old auto_submit_invoices=0 policy are full of these, so they get their own
		# state (and their own action) instead of being mislabelled "not issued".
		return {"key": DRAFT_INVOICE, "days": -days_to_due if days_to_due is not None else None}

	has_invoice = docstatus == 1
	if not has_invoice:
		# A cancelled/deleted invoice reverts the row to Planned (see events.py), so an
		# un-submitted invoice link is treated as "not issued" — never as issued.
		if days_to_due is None:
			return {"key": PLANNED, "days": None}
		if days_to_due < 0:
			return {"key": OVERDUE_UNISSUED, "days": -days_to_due}
		if days_to_due <= max(0, cint(due_soon_days)):
			return {"key": DUE_SOON, "days": -days_to_due}
		return {"key": PLANNED, "days": -days_to_due}

	outstanding = flt(row.get("outstanding"))
	total = flt(row.get("grand_total"))
	if outstanding <= 0.005:
		return {"key": PAID, "days": days_to_due}
	# Money is in, the accountant just hasn't approved the receipt yet — never dun or
	# fine this row, and never show it red.
	if flt(row.get("pending")) >= outstanding - 0.005:
		return {"key": PENDING_RECEIPT, "days": days_to_due}
	if outstanding < total - 0.005:
		return {"key": PARTIALLY_PAID, "days": -days_to_due if days_to_due else 0}
	if days_to_due is not None and days_to_due < 0:
		return {"key": OVERDUE_UNPAID, "days": -days_to_due}
	return {"key": ISSUED, "days": -days_to_due if days_to_due is not None else None}


@frappe.whitelist()
def get_installments(lease_contract):
	"""The lease's rent installments with live health — the Operations Center feed.

	Charges (utilities/CAM) are intentionally NOT merged here: the charge engine groups
	several charge rows into ONE invoice per billing policy, so a row→invoice→action
	mapping would be ambiguous. They get their own panel once this one is proven."""
	lease = frappe.get_doc("Lease Contract", lease_contract)
	lease.check_permission("read")

	rows = frappe.db.sql(
		"""
		SELECT rs.name, rs.period_no, rs.period_start, rs.period_end, rs.due_date,
		       rs.base_amount, rs.status, rs.sales_invoice,
		       si.docstatus AS invoice_docstatus, si.status AS invoice_status,
		       si.base_grand_total AS grand_total, si.outstanding_amount AS outstanding,
		       COALESCE((
		           SELECT SUM(per.allocated_amount)
		           FROM `tabPayment Entry Reference` per
		           JOIN `tabPayment Entry` pe ON pe.name = per.parent
		           WHERE per.reference_doctype = 'Sales Invoice'
		             AND per.reference_name = si.name AND pe.docstatus = 0
		       ), 0) AS pending
		FROM `tabRent Schedule` rs
		LEFT JOIN `tabSales Invoice` si ON si.name = rs.sales_invoice
		WHERE rs.lease_contract = %s
		ORDER BY rs.due_date ASC, rs.period_no ASC
		""",
		lease_contract,
		as_dict=True,
	)

	today = nowdate()
	window = invoicing_policy.due_soon_days()
	policy, _lead = invoicing_policy.current()
	for r in rows:
		r["health"] = compute_health(r, today, window)
	return {
		"policy": policy,
		"issue_on_payment": policy == invoicing_policy.ON_PAYMENT,
		"currency": frappe.get_cached_value("Company", lease.company, "default_currency"),
		"installments": rows,
	}


@frappe.whitelist()
def issue_invoice(schedule):
	"""«إصدار الفاتورة» — issue ONE installment on demand.

	A thin wrapper over the existing generator so the row lock, the Active-lease check,
	the tax-template rules and the idempotency guard are the SAME code the scheduler
	runs. Returns the Sales Invoice name for the client to route to."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	from bunood_realestate.real_estate.tasks import _create_invoice_for_schedule

	existing = frappe.db.get_value("Rent Schedule", schedule, "sales_invoice")
	if existing:
		return {"sales_invoice": existing, "created": False}

	if not _create_invoice_for_schedule(schedule):
		# The generator refuses for a reason the operator must see, not a silent no-op.
		# LOCKING read: a plain re-read can return this transaction's stale snapshot and
		# report "status: Planned" for a row a concurrent worker just invoiced.
		row = frappe.db.get_value(
			"Rent Schedule",
			schedule,
			["status", "sales_invoice", "lease_contract"],
			for_update=True,
			as_dict=True,
		)
		if row and row.sales_invoice:
			return {"sales_invoice": row.sales_invoice, "created": False}
		lease_status = (
			frappe.db.get_value("Lease Contract", row.lease_contract, "status") if row else None
		)
		if lease_status and lease_status != "Active":
			frappe.throw(
				_("Lease {0} is {1} — only an Active lease can be invoiced.").format(
					row.lease_contract, _(lease_status)
				)
			)
		frappe.throw(
			_("This installment cannot be invoiced (status: {0}).").format(
				_(row.status) if row else _("not found")
			)
		)

	si = frappe.db.get_value("Rent Schedule", schedule, "sales_invoice")
	return {"sales_invoice": si, "created": True}


@frappe.whitelist()
def receive_payment(
	schedule=None,
	sales_invoice=None,
	mode_of_payment=None,
	paid_to=None,
	amount=None,
	reference_no=None,
	reference_date=None,
	posting_date=None,
	receipt_file=None,
):
	"""«استلام الدفعة» — the operator's real action: money arrived.

	ORDER IS DELIBERATE AND NEVER REVERSED: the invoice is issued (and committed) BEFORE
	the receipt. If collection then fails we are left with an issued-unpaid invoice —
	recoverable, and the tenant genuinely owes it. The opposite (cash recorded against no
	invoice) would be an unbacked receipt and a ZATCA gap, so it must be impossible.

	The Payment Entry itself is ERPNext's own `get_payment_entry` output: we only fill in
	what the operator told us (mode, bank/cash account, reference, date, amount) and save
	it as a DRAFT for review — bank reconciliation, party accounting and every standard
	cash report keep working because it IS a native Payment Entry. It is left unsubmitted
	on purpose (the agreed review step); a draft touches no GL."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	if not (sales_invoice or schedule):
		frappe.throw(_("Provide an installment or an invoice."))

	# ---- EVERYTHING checkable from the operator's input runs BEFORE issuance ----
	# Issuing submits, and a submitted invoice is ZATCA-reported and uncancellable. A
	# typo or a missing field must never be discovered *after* creating a permanent tax
	# document — so nothing irreversible happens until these all pass.
	if not mode_of_payment:
		frappe.throw(_("Select how the money was received (طريقة الدفع)."))
	if amount is not None and amount != "" and flt(amount) <= 0:
		frappe.throw(_("Received amount must be greater than zero."))

	company = frappe.db.get_value(
		"Sales Invoice" if sales_invoice else "Rent Schedule",
		sales_invoice or schedule,
		"company",
	)
	_assert_cash_account(company, paid_to)

	if not sales_invoice:
		result = issue_invoice(schedule)
		sales_invoice = result["sales_invoice"]
		# Commit the (irreversible, ZATCA-reported) invoice before touching cash, so a
		# later failure can never roll the tax document back out from under ZATCA.
		frappe.db.commit()

	si = frappe.get_doc("Sales Invoice", sales_invoice)
	si.check_permission("read")
	if si.docstatus != 1:
		frappe.throw(_("Invoice {0} is not submitted.").format(sales_invoice))
	outstanding = flt(si.outstanding_amount)
	if outstanding <= 0.005:
		frappe.throw(_("Invoice {0} is already fully paid.").format(sales_invoice))

	amount = flt(amount) if amount else outstanding
	if amount > outstanding + 0.005:
		frappe.throw(
			_("Received amount ({0}) exceeds the outstanding balance ({1}).").format(
				amount, outstanding
			)
		)

	# A DRAFT Payment Entry does not reduce `outstanding_amount`, so two operators
	# collecting the same invoice would each build a full-amount receipt and both could
	# submit — over-collecting the tenant. Surface the existing draft instead.
	pending = frappe.db.sql(
		"""
		SELECT per.parent
		FROM `tabPayment Entry Reference` per
		JOIN `tabPayment Entry` pe ON pe.name = per.parent
		WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name = %s
		  AND pe.docstatus = 0
		LIMIT 1
		""",
		sales_invoice,
	)
	if pending:
		frappe.throw(
			_("A receipt for this invoice is already prepared and awaiting review: {0}").format(
				frappe.utils.get_link_to_form("Payment Entry", pending[0][0])
			)
		)

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	# `bank_account` must go THROUGH get_payment_entry: it derives paid_to,
	# paid_to_account_currency, account type and the paid/received amounts together.
	# Assigning pe.paid_to afterwards would leave the currency/type fields describing
	# the default account — wrong the moment the chosen account has another currency.
	pe = get_payment_entry(
		"Sales Invoice", sales_invoice, party_amount=amount, bank_account=paid_to or None
	)
	pe.mode_of_payment = mode_of_payment
	if posting_date:
		pe.posting_date = posting_date
	if reference_no:
		pe.reference_no = reference_no
	if reference_date:
		pe.reference_date = reference_date
	elif reference_no:
		pe.reference_date = pe.posting_date
	# Property dimension so the receipt is dimension-complete (Phase-0 guard) even when
	# the operator never opens the dimension section.
	if si.get("property"):
		pe.property = si.property
	pe.flags.ignore_permissions = True
	pe.insert()

	if receipt_file:
		_attach_receipt(pe.name, receipt_file)

	return {"payment_entry": pe.name, "sales_invoice": sales_invoice, "amount": amount}


def _assert_cash_account(company, paid_to):
	"""Resolve the bank/cash account NOW, while nothing irreversible has happened yet.
	`get_payment_entry` throws when it cannot find one — discovering that after the
	invoice is issued would leave a permanent tax document behind a fixable typo."""
	if paid_to:
		account = frappe.db.get_value(
			"Account", paid_to, ["company", "account_type", "is_group"], as_dict=True
		)
		if not account or account.is_group or account.company != company:
			frappe.throw(
				_("Select a non-group Bank or Cash account belonging to {0}.").format(company)
			)
		if account.account_type not in ("Bank", "Cash"):
			frappe.throw(_("{0} is not a Bank or Cash account.").format(paid_to))
		return
	from erpnext.accounts.party import get_default_bank_cash_account

	if not (
		get_default_bank_cash_account(company, "Bank") or get_default_bank_cash_account(company, "Cash")
	):
		frappe.throw(
			_("No default Bank or Cash account for {0} — choose where the money was deposited.").format(
				company
			)
		)


def _attach_receipt(payment_entry, file_url):
	"""Re-point the receipt this operator just uploaded at the new Payment Entry.

	Matched narrowly — still unattached AND uploaded by this user, newest first. A bare
	`file_url` lookup is NOT unique (the same file can be attached to many documents), so
	it could hijack another document's attachment and republish a private file elsewhere.
	Failing to link must never cost the operator the payment they just recorded."""
	try:
		name = frappe.db.get_value(
			"File",
			{
				"file_url": file_url,
				"attached_to_name": ["in", ["", None]],
				"owner": frappe.session.user,
			},
			"name",
			order_by="creation desc",
		)
		if not name:
			return
		doc = frappe.get_doc("File", name)
		doc.attached_to_doctype = "Payment Entry"
		doc.attached_to_name = payment_entry
		doc.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(title=f"Bunood: receipt attach failed for {payment_entry}")
