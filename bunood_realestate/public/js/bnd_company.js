// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// Document-expiry (Phase-1 #4): a "Legal Documents" shortcut on the Company form, routing to
// the register filtered to this company's documents. Injected via the doctype_js hook — never
// a core edit. Company is ERPNext core, so we only ADD a button, we never touch its schema.

frappe.ui.form.on("Company", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Legal Documents"),
			() =>
				frappe.set_route("List", "Legal Document", {
					link_doctype: "Company",
					link_name: frm.doc.name,
				}),
			__("Real Estate")
		);
	},
});
