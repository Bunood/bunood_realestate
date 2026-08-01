// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// Renew action: creates the next version (a fresh Active row that supersedes this one) so the
// history is a chain, never an overwrite. Shown only for a live, renewable document.

frappe.ui.form.on("Legal Document", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.is_perpetual) return;
		const live = frm.doc.status === "Active" || frm.doc.status === "Renewal In Progress";
		if (!live) return;

		frappe.db.get_value("RE Document Type", frm.doc.document_type, "renewable").then((r) => {
			if (!r || !r.message || !r.message.renewable) return;
			const btn = frm.add_custom_button(__("Renew"), () => renew_dialog(frm));
			btn.removeClass("btn-default").addClass("btn-primary");

			if (frm.doc.status === "Active") {
				frm.add_custom_button(__("Mark Renewal In Progress"), () => {
					frm.set_value("status", "Renewal In Progress");
					frm.save();
				});
			}
		});

		if (frm.doc.supersedes) {
			frm.add_custom_button(__("Previous Version"), () =>
				frappe.set_route("Form", "Legal Document", frm.doc.supersedes)
			);
		}
	},
});

function renew_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Renew {0}", [frm.doc.document_type]),
		fields: [
			{
				fieldname: "new_expiry_date",
				label: __("New Expiry Date"),
				fieldtype: "Date",
				reqd: 1,
			},
			{
				fieldname: "new_document_number",
				label: __("New Document Number"),
				fieldtype: "Data",
				default: frm.doc.document_number,
				description: __("Leave as-is if the number is unchanged."),
			},
			{
				fieldname: "new_issue_date",
				label: __("New Issue Date"),
				fieldtype: "Date",
				default: frappe.datetime.get_today(),
			},
		],
		primary_action_label: __("Create Renewal"),
		primary_action(v) {
			frappe.call({
				method: "bunood_realestate.real_estate.doctype.legal_document.legal_document.renew_document",
				args: {
					name: frm.doc.name,
					new_expiry_date: v.new_expiry_date,
					new_document_number: v.new_document_number,
					new_issue_date: v.new_issue_date,
				},
				freeze: true,
				callback: (r) => {
					if (r.message) {
						d.hide();
						frappe.show_alert({ message: __("Renewal created."), indicator: "green" });
						frappe.set_route("Form", "Legal Document", r.message);
					}
				},
			});
		},
	});
	d.show();
}
