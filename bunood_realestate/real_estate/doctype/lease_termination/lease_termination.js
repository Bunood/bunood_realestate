// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Lease Termination", {
	refresh(frm) {
		// Draft only: pull suggested move-out charges into the deductions (once each).
		if (frm.doc.docstatus === 0 && !frm.is_new() && (frm.doc.inspection || []).length) {
			frm.add_custom_button(__("Pull Inspection Charges"), () => {
				frappe.call({
					method: "bunood_realestate.real_estate.doctype.lease_termination.lease_termination.pull_inspection_charges",
					args: { lease_termination: frm.doc.name },
					freeze: true,
					freeze_message: __("Pulling charges..."),
					callback: (r) => {
						const n = (r.message && r.message.added) || 0;
						frappe.show_alert({
							message: n ? __("Added {0} deduction(s)", [n]) : __("No new charges to pull"),
							indicator: n ? "green" : "orange",
						});
						frm.reload_doc();
					},
				});
			});
		}
	},
});
