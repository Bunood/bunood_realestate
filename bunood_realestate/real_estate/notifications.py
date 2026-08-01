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
# Document-expiry pure core (Phase-1 #4) — offline-testable, no DB.
# Government/compliance documents get a LONGER lead than leases (renewals are slow) plus a
# 0-day "expires today" beat. All four functions are DB-free so they run in the shim runner.
# ---------------------------------------------------------------------------

DOC_EXPIRY_MILESTONES = (90, 30, 7, 0)  # days before expiry_date (system default)
GRACE_MILESTONE = "GRACE"  # sentinel beat: one alert while a lapsed doc is still within grace


def parse_reminder_days(spec, default=DOC_EXPIRY_MILESTONES):
	"""Pure: a per-type Reminder Days spec ('90,30,7,0') -> a descending, de-duped tuple of
	non-negative ints. Blank / all-invalid falls back to the system default, so a bad or empty
	value can never leave a document with no reminder milestones."""
	if not spec:
		return default
	out = set()
	for part in str(spec).replace(" ", "").split(","):
		if not part:
			continue
		try:
			v = int(part)
		except ValueError:
			continue
		if v >= 0:
			out.add(v)
	return tuple(sorted(out, reverse=True)) if out else default


def current_milestone(expiry, today, milestones=DOC_EXPIRY_MILESTONES):
	"""Catch-up-safe: the TIGHTEST milestone bucket `today` falls into for a document expiring
	on `expiry` — the smallest m with 0 <= days_left <= m — or None if still outside the widest
	window (or already past). Depends on days_left, never on hitting an exact day, so a missed
	scheduler day cannot skip a positive milestone (e.g. T-90/30/7); the T-0 "expires today" beat
	is the exception — an already-past row is intentionally dropped (no false "expires today" on a
	lapsed document), and the tightest positive beat already fired. Each bucket fires once (the log
	key embeds the milestone), so returning the same tightest bucket on consecutive days is deduped."""
	days_left = (getdate(expiry) - getdate(today)).days
	reached = [m for m in milestones if 0 <= days_left <= m]
	return min(reached) if reached else None


def document_should_alert(row, today, milestones=None, grace_days=0):
	"""The single tested 'which documents expire' predicate. row: {is_perpetual, expiry_date,
	status}. Perpetual / blank-expiry / non-Active short-circuit to None (the deed guard). Before
	expiry: the per-type milestone bucket (or None). After expiry but still within grace_days:
	the GRACE sentinel (one escalated alert). Past grace: None."""
	if row.get("is_perpetual") or not row.get("expiry_date"):
		return None
	if (row.get("status") or "Active") != "Active":
		return None
	milestones = milestones or DOC_EXPIRY_MILESTONES
	days_left = (getdate(row["expiry_date"]) - getdate(today)).days
	if days_left >= 0:
		return current_milestone(row["expiry_date"], today, milestones)
	if grace_days and -days_left <= int(grace_days):
		return GRACE_MILESTONE
	return None


def document_reminder_detail(legal_document, expiry, m):
	"""Idempotency key. Embeds `expiry` so a RENEWAL-IN-PLACE (same row, new date) re-arms
	instead of being suppressed by last cycle's log; embeds the doc name so it is globally
	unique -> the reminder log's single-column UNIQUE index holds under a concurrent race."""
	return f"{legal_document}|{getdate(expiry)}|T-{m}"


def document_status(expiry, today, is_perpetual, grace_days=0):
	"""Report/chip status chip — pure. Perpetual / blank -> Perpetual; expired but within grace
	-> In Grace; past grace -> Expired; within 30 days -> Due Soon; else OK."""
	if is_perpetual or not expiry:
		return "Perpetual"
	d = (getdate(expiry) - getdate(today)).days
	if d < 0:
		return "In Grace" if grace_days and -d <= int(grace_days) else "Expired"
	return "Due Soon" if d <= 30 else "OK"


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


def _unissued_overdue(lease_contract, today):
	"""Rent that is past due but has no invoice yet (non-auto issuance policies). Ex-VAT
	by nature — it is an installment, not yet a tax document."""
	value = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_amount), 0)
		FROM `tabRent Schedule`
		WHERE lease_contract = %s AND status = 'Planned'
		  AND (sales_invoice IS NULL OR sales_invoice = '')
		  AND due_date < %s
		""",
		(lease_contract, today),
	)
	return flt(value[0][0]) if value else 0.0


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
			# Invoiced-and-unpaid (the GL truth) PLUS anything past due but not yet issued
			# (under Manual / On Payment the tenant genuinely owes it), MINUS receipts the
			# operator already recorded and that are only awaiting approval — dunning a
			# tenant for money sitting in the till is the fastest way to lose them.
			from bunood_realestate.real_estate.operations import pending_receipts_for_customer

			outstanding = (
				_tenant_outstanding(lease.customer, lease.company)
				+ _unissued_overdue(lease.name, today)
				- pending_receipts_for_customer(lease.customer, lease.company)
			)
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
# Auto-draft renewals — completes the renewal loop (reuses renew_lease; no new path)
# ---------------------------------------------------------------------------

AUTO_RENEW_WINDOW = 30  # days before end_date to prepare the renewal draft


def auto_draft_renewals():
	"""Daily scheduler entry — no-op unless enabled in settings."""
	if not frappe.db.get_single_value("Real Estate Settings", "auto_draft_renewals"):
		return 0
	return _run_auto_renewals()


@frappe.whitelist()
def run_auto_renewals_now():
	"""Manual trigger (desk button). Ignores the settings gate; role-gated."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return _run_auto_renewals()


def _run_auto_renewals(today=None):
	"""For each Active lease flagged auto_renew and expiring within the window, create a
	Draft renewal (via the existing renew_lease — the ONE renewal path) for the operator
	to review and submit. Idempotent: a lease that already has a renewal is skipped."""
	from bunood_realestate.real_estate.doctype.lease_contract.lease_contract import renew_lease

	today = getdate(today or nowdate())
	until = add_days(today, AUTO_RENEW_WINDOW)
	leases = frappe.get_all(
		"Lease Contract",
		filters={"status": "Active", "docstatus": 1, "auto_renew": 1, "end_date": ["between", [today, until]]},
		fields=["name"],
	)
	created = 0
	for lease in leases:
		try:
			if frappe.db.exists("Lease Contract", {"parent_lease": lease.name}):
				continue  # already has a renewal — never double-draft
			renew_lease(lease.name)
			created += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: auto-renewal draft failed",
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
# Document-expiry alerts (Phase-1 #4) — the ONE sweep over the Legal Document register.
# Same shape as the lease sweep: settings-gated daily entry + role-gated manual trigger,
# Document-Reminder-Log-row-as-idempotency-key, best-effort delivery, per-row fail-loud.
# ---------------------------------------------------------------------------

def notify_expiring_documents():
	"""Daily scheduler entry — no-op unless enabled in settings."""
	if not frappe.db.get_single_value("Real Estate Settings", "enable_document_expiry_alerts"):
		return 0
	return _run_document_expiry_alerts()


@frappe.whitelist()
def run_document_expiry_alerts_now():
	"""Manual trigger (desk button). Ignores the settings gate; role-gated."""
	frappe.only_for(["Accounts Manager", "System Manager"])
	return _run_document_expiry_alerts()


# The sweep query bounds. Reminder leads and grace periods are per-type, so the window is
# widened generously on both sides; the per-row predicate then decides using each type's policy.
# A type configured beyond these caps still gets its tighter beats — only an extreme (>180-day)
# lead/grace beyond the window would be missed, which no real Saudi document uses.
DOC_EXPIRY_MAX_LEAD = 180
DOC_EXPIRY_MAX_GRACE = 180


def _doc_type_policy(document_type, cache):
	"""(milestones, grace_days) for a document type, resolved from RE Document Type and cached
	per run. Blank Reminder Days falls back to the system default; a missing type is default/0."""
	if document_type not in cache:
		row = frappe.db.get_value(
			"RE Document Type", document_type, ["reminder_days", "grace_days"], as_dict=True
		) or frappe._dict()
		cache[document_type] = (parse_reminder_days(row.get("reminder_days")), int(row.get("grace_days") or 0))
	return cache[document_type]


def _run_document_expiry_alerts(today=None):
	"""Windowed (not exact-date) so a missed scheduler day is caught up: every Active,
	non-perpetual register row whose expiry falls in the widened horizon is re-evaluated with
	its TYPE's reminder-days + grace policy; document_should_alert picks the beat and the log
	key dedups. The window spans grace (past) to the longest lead (future)."""
	today = getdate(today or nowdate())
	lo = add_days(today, -DOC_EXPIRY_MAX_GRACE)
	hi = add_days(today, DOC_EXPIRY_MAX_LEAD)
	rows = frappe.get_all(
		"Legal Document",
		filters={"is_perpetual": 0, "status": "Active", "expiry_date": ["between", [lo, hi]]},
		fields=[
			"name", "document_type", "link_doctype", "link_name", "company",
			"expiry_date", "document_number", "is_perpetual", "status",
		],
	)
	policy_cache = {}
	created = 0
	for r in rows:
		try:
			if _emit_document_alert(r, today, policy_cache):
				created += 1
			frappe.db.commit()
		except (frappe.UniqueValidationError, frappe.exceptions.DuplicateEntryError):
			# The log's UNIQUE `detail` index rejected a manual-vs-scheduler double-insert.
			# A unique-FIELD (not primary-key) violation raises UniqueValidationError; the
			# alert already went out on the winning thread — harmless, never a logged error.
			frappe.db.rollback()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bunood: document-expiry alert failed",
				message=f"Legal Document {r.name}\n\n{frappe.get_traceback()}",
			)
	return created


def _emit_document_alert(row, today, policy_cache=None):
	milestones, grace_days = _doc_type_policy(row.document_type, policy_cache if policy_cache is not None else {})
	m = document_should_alert(row, today, milestones=milestones, grace_days=grace_days)
	if m is None:
		return False
	detail = document_reminder_detail(row.name, row.expiry_date, m)
	if frappe.db.exists("Document Reminder Log", {"detail": detail}):
		return False  # the log row IS the idempotency key
	type_label = frappe.db.get_value("RE Document Type", row.document_type, "document_type_name") or row.document_type
	# Report the ACTUAL days remaining (not the milestone bucket) so the message is truthful
	# even when a document enters the register already inside a tighter window.
	days_left = (getdate(row.expiry_date) - getdate(today)).days
	if m == GRACE_MILESTONE:
		when = _("expired {0} days ago — within its grace period").format(-days_left)
	elif days_left <= 0:
		when = _("today")
	else:
		when = _("in {0} days").format(days_left)
	msg = _("{0} ({1}) for {2} {3} expires {4} — on {5}. Renew before it lapses.").format(
		type_label, row.document_number or "-", row.link_doctype or "-", row.link_name or "-", when, row.expiry_date,
	)
	_log_document_reminder(row, m, detail, msg)
	_notify_operators(
		row.company, _("Document expiring soon"), msg,
		reference=row.name, reference_doctype="Legal Document",
	)
	return True


def _log_document_reminder(row, m, detail, message):
	doc = frappe.get_doc({
		"doctype": "Document Reminder Log",
		"legal_document": row.name,
		"document_type": row.document_type,
		"link_doctype": row.link_doctype,
		"link_name": row.link_name,
		"company": row.company,
		"milestone": f"T-{m}",
		"expiry_date": row.expiry_date,
		"detail": detail,
		"message": message,
		"sent_on": frappe.utils.now_datetime(),
	})
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


@frappe.whitelist()
def upcoming_document_expiries(days=90):
	"""Documents expiring within `days` across the caller's permitted companies (preview button)."""
	companies = frappe.get_list("Company", pluck="name") or []
	if not companies:
		return []
	until = add_days(nowdate(), int(days))
	return frappe.get_all(
		"Legal Document",
		filters={
			"is_perpetual": 0, "status": "Active",
			"company": ["in", companies],
			"expiry_date": ["between", [nowdate(), until]],
		},
		fields=["name", "document_type", "link_doctype", "link_name", "expiry_date", "document_number", "company"],
		order_by="expiry_date asc",
	)


@frappe.whitelist()
def expiring_documents_count(days=30):
	"""Count of Active, non-perpetual documents expiring within `days` across the caller's
	permitted companies — feeds the Real Estate workspace insight chip ('Docs expiring ≤30d')."""
	companies = frappe.get_list("Company", pluck="name") or []
	if not companies:
		return 0
	return frappe.db.count(
		"Legal Document",
		{
			"is_perpetual": 0, "status": "Active", "company": ["in", companies],
			"expiry_date": ["between", [nowdate(), add_days(nowdate(), int(days))]],
		},
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


def _notify_operators(company, subject, message, reference, reference_doctype="Lease Contract"):
	"""Best-effort in-app notification to the company's Accounts Managers. Never raises —
	delivery must not break the sweep or its audit log. The deep-link doctype is a parameter
	(defaults to Lease Contract for the existing callers) so a Legal Document alert links to
	the register row, not a non-existent lease."""
	try:
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		users = _accounts_managers(company)
		if not users:
			return
		enqueue_create_notification(users, {
			"type": "Alert",
			"subject": f"{subject} — {reference}",
			"email_content": message,
			"document_type": reference_doctype,
			"document_name": reference,
		})
	except Exception:
		pass


def _accounts_managers(company=None):
	"""Accounts Managers to alert. When a company is given, restrict to managers permitted for
	it — a manager with a Company User Permission for a DIFFERENT company is excluded, so a
	multi-company site never cross-notifies. A manager with no Company restriction (or a site
	with none configured) is treated as unrestricted, so delivery is never narrower than before."""
	rows = frappe.get_all(
		"Has Role",
		filters={"role": "Accounts Manager", "parenttype": "User"},
		pluck="parent",
	)
	managers = [u for u in set(rows) if u not in ("Administrator", "Guest")]
	if not company or not managers:
		return managers
	# Only a Company User Permission that applies to ALL doctypes is a real, site-wide company
	# lock. A UP with `applicable_for` set restricts the user for that ONE doctype only (e.g. a
	# Company-scoped Sales Invoice permission) and must NOT narrow alert delivery — otherwise a
	# manager who fully manages this company but has an unrelated doctype-scoped UP would be
	# silently dropped (regressing the lease/overdue sweeps too). Honor the "never narrower" rule.
	perms = frappe.get_all(
		"User Permission",
		filters={"allow": "Company", "user": ["in", managers], "apply_to_all_doctypes": 1},
		fields=["user", "for_value"],
	)
	restricted = {}
	for p in perms:
		restricted.setdefault(p.user, set()).add(p.for_value)
	# Keep a manager who is unrestricted (no global Company user-permission) OR permitted here.
	return [u for u in managers if u not in restricted or company in restricted[u]]
