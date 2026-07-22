# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Collections — automatic late fees + tenant dunning (WhatsApp).

Late fees are posted through the existing Charge engine (Charge -> Sales Invoice),
so all money stays native to ERPNext. Idempotency: each overdue Rent Schedule row
is charged at most once, guarded by its `late_fee_charge` link under a row lock —
the same pattern used by rent generation, head-lease bills and owner payout.
"""

import re
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate


def compute_late_fee(overdue_amount, fee_type, value, cap):
	"""Pure & testable: the late fee for one overdue period."""
	overdue = flt(overdue_amount)
	value = flt(value)
	cap = flt(cap)
	if overdue <= 0 or value <= 0:
		return 0.0
	if fee_type == "Fixed Amount":
		fee = value
	else:  # "Percentage of Overdue"
		fee = flt(overdue * value / 100.0, 2)
	if cap and fee > cap:
		fee = cap
	return flt(fee, 2)


def apply_late_fees(lease_contract=None):
	"""Daily scheduler / manual: charge a late fee on every overdue, still-unpaid rent
	invoice past the grace period. Idempotent, per-row transaction, fail-loud-per-row."""
	settings = frappe.get_single("Real Estate Settings")
	if not settings.enable_late_fees or not settings.late_fee_charge_type:
		return 0

	grace = int(settings.late_fee_grace_days or 0)
	cutoff = add_days(nowdate(), -grace)

	filters = {
		"status": "Invoiced",
		"sales_invoice": ["is", "set"],
		"late_fee_charge": ["in", [None, ""]],
		"due_date": ["<=", cutoff],
	}
	if lease_contract:
		filters["lease_contract"] = lease_contract
	names = frappe.get_all("Rent Schedule", filters=filters, order_by="due_date asc", pluck="name")

	created = 0
	for name in names:
		try:
			if _charge_late_fee(name, settings):
				created += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: late-fee generation failed",
				message=f"Rent Schedule {name}\n\n{frappe.get_traceback()}",
			)
	return created


def _charge_late_fee(schedule_name, settings):
	from bunood_realestate.core.charge import _apply, post_reference_charges

	# Row lock + re-read → the idempotency guard below can't be raced.
	frappe.db.get_value("Rent Schedule", schedule_name, "name", for_update=True)
	row = frappe.get_doc("Rent Schedule", schedule_name)
	if row.late_fee_charge or row.status != "Invoiced" or not row.sales_invoice:
		return False

	# Only fee a genuinely-unpaid invoice (never a settled one).
	outstanding = flt(frappe.db.get_value("Sales Invoice", row.sales_invoice, "outstanding_amount"))
	if outstanding <= 0:
		return False

	fee = compute_late_fee(outstanding, settings.late_fee_type, settings.late_fee_value, settings.late_fee_cap)
	if fee <= 0:
		return False

	charge_name = _apply(
		charge_type=settings.late_fee_charge_type,
		party=row.customer,
		party_type="Customer",
		amount=fee,
		company=row.company,
		reference_doctype="Rent Schedule",
		reference_name=row.name,
		remarks=_("Late fee — period {0}, due {1}").format(row.period_no, row.due_date),
	)
	# Stamp the idempotency key BEFORE posting: if the post fails, the row is still
	# marked so we never double-charge; the Pending charge is posted later by the
	# normal "Post Fee Charges" flow.
	frappe.db.set_value("Rent Schedule", row.name, "late_fee_charge", charge_name)
	post_reference_charges("Rent Schedule", row.name)
	return True


@frappe.whitelist()
def run_late_fees_now(lease_contract=None):
	"""Manual trigger (button). Same rules as the scheduled job."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return apply_late_fees(lease_contract=lease_contract)


# ---------------------------------------------------------------------------
# Dunning — WhatsApp click-to-send links (no gateway needed; opens wa.me).
# ---------------------------------------------------------------------------

def _wa_number(mobile):
	"""Normalise a Saudi mobile to intl digits for wa.me (best effort; blank ok)."""
	digits = re.sub(r"\D", "", mobile or "")
	if not digits:
		return ""
	if digits.startswith("00"):
		digits = digits[2:]
	if digits.startswith("966"):
		return digits
	if digits.startswith("0"):
		return "966" + digits[1:]
	if len(digits) == 9:  # bare 5XXXXXXXX
		return "966" + digits
	return digits


def _tenant_outstanding(customer, company):
	"""Total unpaid across the tenant's submitted rent invoices."""
	res = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(outstanding_amount), 0)
		FROM `tabSales Invoice`
		WHERE docstatus = 1 AND customer = %s AND company = %s AND outstanding_amount > 0
		""",
		(customer, company),
	)
	return flt(res[0][0]) if res else 0.0


@frappe.whitelist()
def dues_whatsapp_link(lease_contract):
	"""Build a click-to-send WhatsApp dunning link for a lease's tenant + log it."""
	frappe.has_permission("Lease Contract", "read", throw=True)
	lease = frappe.get_doc("Lease Contract", lease_contract)
	mobile = frappe.db.get_value("Customer", lease.customer, "mobile_no") or ""
	outstanding = _tenant_outstanding(lease.customer, lease.company)
	tenant_name = frappe.db.get_value("Customer", lease.customer, "customer_name") or lease.customer

	currency = frappe.get_cached_value("Company", lease.company, "default_currency") or "SAR"
	msg = _("عميلنا العزيز {0}، نودّ تذكيركم بوجود مبلغ مستحق قدره {1} {2} على عقد الإيجار {3}. نأمل السداد في أقرب وقت. شكرًا لكم.").format(
		tenant_name, frappe.utils.fmt_money(outstanding, currency=currency), currency, lease.name
	)
	link = f"https://wa.me/{_wa_number(mobile)}?text={quote(msg)}"

	_log_notification(lease, "WhatsApp", outstanding, msg)
	return {"link": link, "outstanding": outstanding, "mobile": mobile}


def _log_notification(lease, channel, amount, message):
	doc = frappe.get_doc({
		"doctype": "Collection Notification",
		"lease_contract": lease.name,
		"customer": lease.customer,
		"company": lease.company,
		"channel": channel,
		"amount": amount,
		"message": message,
		"sent_on": frappe.utils.now_datetime(),
	})
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name
