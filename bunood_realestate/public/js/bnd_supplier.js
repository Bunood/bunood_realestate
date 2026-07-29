// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// Wrapper doctrine (plan-financial-reporting.md Phase 3): the Owner Ledger is one click
// from the owner's Supplier form — injected via the doctype_js hook (never a core edit).
// Shown only for suppliers that actually own properties.

frappe.ui.form.on("Supplier", {
	refresh(frm) {
		if (frm.is_new()) return;
		frappe.db
			.count("Property", { filters: { owner_party: frm.doc.name } })
			.then((n) => {
				if (!n) return;
				frm.add_custom_button(
					__("Owner Ledger"),
					() => frappe.set_route("query-report", "Owner Ledger", { owner: frm.doc.name }),
					__("Real Estate")
				);
			});
	},
});
