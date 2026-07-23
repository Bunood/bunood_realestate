// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Maintenance Request", {
	refresh(frm) {
		if (frm.is_new()) return;
		// Post a conversation update (message + optional photo). Server stamps the
		// author/time and enforces write permission on this request.
		frm.add_custom_button(__("Post Update"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Post Update"),
				fields: [
					{ fieldname: "message", fieldtype: "Small Text", label: __("Message") },
					{ fieldname: "photo", fieldtype: "Attach Image", label: __("Photo") },
				],
				primary_action_label: __("Post"),
				primary_action(values) {
					if (!values.message && !values.photo) {
						frappe.msgprint(__("Write a message or attach a photo."));
						return;
					}
					frappe.call({
						method: "bunood_realestate.real_estate.doctype.maintenance_request.maintenance_request.add_update",
						args: { request: frm.doc.name, message: values.message, photo: values.photo },
						freeze: true,
						freeze_message: __("Posting..."),
						callback: () => {
							d.hide();
							frm.reload_doc();
						},
					});
				},
			});
			d.show();
		});
	},
});
