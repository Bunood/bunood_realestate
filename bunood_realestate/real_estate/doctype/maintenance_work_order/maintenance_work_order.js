// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Maintenance Work Order", {
	refresh(frm) {
		// Post the contractor cost to the GL once the work is Done — explicit action, so a
		// completed work order's spend hits per-property P&L (never coupled to the save).
		if (!frm.is_new() && frm.doc.status === "Done" && frm.doc.contractor && flt(frm.doc.total_cost) > 0) {
			if (frm.doc.purchase_invoice) {
				frm.add_custom_button(__("Open Contractor Bill"), () =>
					frappe.set_route("Form", "Purchase Invoice", frm.doc.purchase_invoice)
				);
			} else {
				frm.add_custom_button(__("Post Contractor Bill"), () => {
					frappe.call({
						method: "bunood_realestate.real_estate.doctype.maintenance_work_order.maintenance_work_order.post_contractor_bill",
						args: { work_order: frm.doc.name },
						freeze: true,
						freeze_message: __("Posting contractor bill..."),
						callback: (r) => {
							if (r.message && r.message.purchase_invoice) {
								frappe.show_alert({ message: __("Contractor bill posted."), indicator: "green" });
								frm.reload_doc();
							}
						},
					});
				});
			}
		}
	},
});
