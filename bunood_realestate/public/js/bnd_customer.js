// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// Wrapper doctrine (plan-financial-reporting.md Phase 3): the tenant's statement is one
// click from the Customer form — a preset entry over our report, injected via the
// doctype_js hook (never a core edit). Shown only for actual tenants (has leases).

frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new()) return;
		frappe.db
			.count("Lease Contract", { filters: { customer: frm.doc.name, docstatus: 1 } })
			.then((n) => {
				if (!n) return;
				frm.add_custom_button(
					__("Tenant Statement"),
					() => frappe.set_route("query-report", "Customer Statement", { customer: frm.doc.name }),
					__("Real Estate")
				);
			});
	},
});
