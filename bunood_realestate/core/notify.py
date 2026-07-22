# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""bunood_realestate.core.notify — one call, many channels.

This facade EARNS its place: it fans a single notify() call out to Email / SMS /
WhatsApp. That is value Frappe's raw APIs don't give in one call — unlike wrapping
`frappe.sendmail` 1:1 (which we deliberately do NOT do)."""

import frappe


def _as_list(value):
	if not value:
		return []
	if isinstance(value, str):
		return [v.strip() for v in value.split(",") if v.strip()]
	return list(value)


@frappe.whitelist()
def notify(recipients=None, subject=None, message=None, channels=None,
           mobiles=None, reference_doctype=None, reference_name=None):
	"""Send `message` over one or more channels.
	- email  -> `recipients` (email addresses)
	- sms    -> `mobiles` (phone numbers; falls back to `recipients` only if given as phones)
	- whatsapp -> `mobiles`
	channels: list/tuple from {"email","sms","whatsapp"} (default ["email"])."""
	recipients = _as_list(recipients)
	mobiles = _as_list(mobiles)
	channels = channels or ["email"]
	if isinstance(channels, str):
		channels = [channels]
	sent = []

	if "email" in channels and recipients:
		frappe.sendmail(
			recipients=recipients,
			subject=subject or "",
			message=message or "",
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
		sent.append("email")

	if "sms" in channels and mobiles:
		try:
			from frappe.core.doctype.sms_settings.sms_settings import send_sms

			send_sms(mobiles, message or "")
			sent.append("sms")
		except Exception:
			frappe.log_error(title="Bunood notify: SMS failed", message=frappe.get_traceback())

	if "whatsapp" in channels and mobiles:
		# Pluggable: wire a WhatsApp provider here (own hook / app). Logged for now.
		frappe.logger("bunood").info("WhatsApp channel requested but not configured")

	return {"sent": sent}
