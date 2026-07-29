// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Lease Termination", {
	refresh(frm) {
		// Both buttons call server methods that read the SAVED document and then reload —
		// unsaved grid edits would be invisible to the server and silently discarded by the
		// reload. Save first when dirty, then run.
		const call_after_save = (method, freeze_message, on_done) => {
			const run = () =>
				frappe.call({
					method: method,
					args: { lease_termination: frm.doc.name },
					freeze: true,
					freeze_message: freeze_message,
					callback: (r) => {
						on_done(r);
						frm.reload_doc();
					},
				});
			if (frm.is_dirty()) frm.save().then(run);
			else run();
		};

		// Draft only: pre-fill the move-out inspection from the lease's handover snapshot
		// (what was actually delivered at move-in). Idempotent server-side.
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Load Handover Checklist"), () => {
				call_after_save(
					"bunood_realestate.real_estate.doctype.lease_termination.lease_termination.load_handover_checklist",
					__("Loading checklist..."),
					(r) => {
						const n = (r.message && r.message.added) || 0;
						frappe.show_alert({
							message: n ? __("Added {0} checklist item(s)", [n]) : __("Checklist already loaded (or no handover snapshot)"),
							indicator: n ? "green" : "orange",
						});
					}
				);
			});
		}

		// Draft only: pull suggested move-out charges into the deductions (once each).
		if (frm.doc.docstatus === 0 && !frm.is_new() && (frm.doc.inspection || []).length) {
			frm.add_custom_button(__("Pull Inspection Charges"), () => {
				call_after_save(
					"bunood_realestate.real_estate.doctype.lease_termination.lease_termination.pull_inspection_charges",
					__("Pulling charges..."),
					(r) => {
						const n = (r.message && r.message.added) || 0;
						frappe.show_alert({
							message: n ? __("Added {0} deduction(s)", [n]) : __("No new charges to pull"),
							indicator: n ? "green" : "orange",
						});
					}
				);
			});
		}
	},
});
