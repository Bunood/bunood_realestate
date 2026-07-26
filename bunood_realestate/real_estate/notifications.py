# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Renewal & collection notifications (§10).

Two idempotent daily sweeps, gated by Real Estate Settings.enable_auto_notifications:

  * notify_expiring_leases  — alert on Active leases expiring in T-60 / T-30 / T-7 days
    (drives renewals). One alert per (lease, milestone), ever.
  * send_overdue_reminders  — remind on tenants with unpaid rent. One per (lease, day).

Every alert is recorded in the Collection Notification audit log; the log row IS the
idempotency key (notification_type + detail), so a re-run — or a second worker — never
double-sends. Actual email/system delivery is best-effort (guarded) so a missing SMTP
config can never break the sweep; the audit log is always written. Manual whitelisted
triggers (role-gated) power the desk buttons and ignore the settings gate.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

EXPIRY_MILESTONES = (60, 30, 7)  # days before end_date


def expiry_milestone(start, end, today):
	"""Pure & testable: which milestone (60/30/7) does `today` hit for a lease ending
	on `end`, or None. `today` hits milestone m when end == today + m days."""
	end = getdate(end)
	today = getdate(today)
	days_left = (end - today).days
	return days_left if days_left in EXPIRY_MILESTONES else None


# ---------------------------------------------------------------------------
# Renewal / expiry alerts
# ---------------------------------------------------------------------------

def notify_expiring_leases():
	"""Daily scheduler entry — no-op unless enabled in settings."""
	if not frappe.db.get_single_value("Real Estate Settings", "enable_auto_notifications"):
		return 0
	return _run_expiry_alerts()


@frappe.whitelist()
def run_expiry_alerts_now():
	"""Manual trigger (desk button). Ignores the settings gate; role-gated."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return _run_expiry_alerts()


def _run_expiry_alerts(today=None):
	today = getdate(today or nowdate())
	created = 0
	for milestone in EXPIRY_MILESTONES:
		target = add_days(today, milestone)
		leases = frappe.get_all(
			"Lease Contract",
			filters={"status": "Active", "docstatus": 1, "end_date": target},
			fields=["name", "customer", "company", "property", "end_date", "auto_renew"],
		)
		for lease in leases:
			try:
				if _emit_expiry_alert(lease, milestone):
					created += 1
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(
					title="Bunood: lease-expiry alert failed",
					message=f"Lease {lease.name} (T-{milestone})\n\n{frappe.get_traceback()}",
				)
	return created


def _emit_expiry_alert(lease, milestone):
	detail = f"T-{milestone}"
	if frappe.db.exists(
		"Collection Notification",
		{"lease_contract": lease.name, "notification_type": "Renewal", "detail": detail},
	):
		return False

	tenant_name = frappe.db.get_value("Customer", lease.customer, "customer_name") or lease.customer
	renew_note = _("Auto-renew is ON.") if lease.auto_renew else _("Auto-renew is OFF — contact the tenant to renew.")
	msg = _("Lease {0} for {1} (property {2}) expires on {3} — in {4} days. {5}").format(
		lease.name, tenant_name, lease.property or "-", lease.end_date, milestone, renew_note
	)

	_log(lease, notification_type="Renewal", channel="System", detail=detail, amount=0, message=msg)
	_notify_operators(lease.company, _("Lease expiring soon"), msg, lease.name)
	return True


# ---------------------------------------------------------------------------
# Overdue collection reminders
# ---------------------------------------------------------------------------

def send_overdue_reminders():
	"""Daily scheduler entry — no-op unless enabled in settings."""
	if not frappe.db.get_single_value("Real Estate Settings", "enable_auto_notifications"):
		return 0
	return _run_overdue_reminders()


@frappe.whitelist()
def run_overdue_reminders_now():
	"""Manual trigger (desk button). Ignores the settings gate; role-gated."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return _run_overdue_reminders()


def _run_overdue_reminders(today=None):
	from bunood_realestate.real_estate.collections import _tenant_outstanding

	today = getdate(today or nowdate())
	detail = str(today)
	created = 0
	leases = frappe.get_all(
		"Lease Contract",
		filters={"status": "Active", "docstatus": 1},
		fields=["name", "customer", "company"],
	)
	for lease in leases:
		try:
			outstanding = _tenant_outstanding(lease.customer, lease.company)
			if outstanding <= 0:
				continue
			if frappe.db.exists(
				"Collection Notification",
				{"lease_contract": lease.name, "notification_type": "Collection", "detail": detail},
			):
				continue
			currency = frappe.get_cached_value("Company", lease.company, "default_currency") or "SAR"
			tenant_name = frappe.db.get_value("Customer", lease.customer, "customer_name") or lease.customer
			msg = _("Reminder: {0} has an outstanding balance of {1} on lease {2}.").format(
				tenant_name, frappe.utils.fmt_money(outstanding, currency=currency), lease.name
			)
			_log(lease, notification_type="Collection", channel="System", detail=detail, amount=outstanding, message=msg)
			_notify_operators(lease.company, _("Overdue rent"), msg, lease.name)
			created += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: overdue reminder failed",
				message=f"Lease {lease.name}\n\n{frappe.get_traceback()}",
			)
	return created


# ---------------------------------------------------------------------------
# Preview data (desk buttons) — no side effects
# ---------------------------------------------------------------------------

@frappe.whitelist()
def upcoming_renewals(days=90):
	"""Leases expiring within `days` (default 90) across the caller's permitted companies —
	powers the 'Renewals' preview button / pipeline."""
	companies = frappe.get_list("Company", pluck="name") or []
	if not companies:
		return []
	until = add_days(nowdate(), int(days))
	return frappe.get_all(
		"Lease Contract",
		filters={
			"status": "Active", "docstatus": 1,
			"company": ["in", companies],
			"end_date": ["between", [nowdate(), until]],
		},
		fields=["name", "customer", "property", "end_date", "annual_rent_total", "auto_renew"],
		order_by="end_date asc",
	)


# ---------------------------------------------------------------------------
# Delivery + logging helpers
# ---------------------------------------------------------------------------

def _log(lease, notification_type, channel, detail, amount, message):
	doc = frappe.get_doc({
		"doctype": "Collection Notification",
		"lease_contract": lease.name,
		"customer": lease.customer,
		"company": lease.company,
		"notification_type": notification_type,
		"channel": channel,
		"detail": detail,
		"amount": flt(amount),
		"message": message,
		"sent_on": frappe.utils.now_datetime(),
	})
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def _notify_operators(company, subject, message, reference):
	"""Best-effort in-app notification to the company's Accounts Managers. Never raises —
	delivery must not break the sweep or its audit log."""
	try:
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		users = _accounts_managers()
		if not users:
			return
		enqueue_create_notification(users, {
			"type": "Alert",
			"subject": f"{subject} — {reference}",
			"email_content": message,
			"document_type": "Lease Contract",
			"document_name": reference,
		})
	except Exception:
		pass


def _accounts_managers():
	rows = frappe.get_all(
		"Has Role",
		filters={"role": "Accounts Manager", "parenttype": "User"},
		pluck="parent",
	)
	return [u for u in set(rows) if u not in ("Administrator", "Guest")]
