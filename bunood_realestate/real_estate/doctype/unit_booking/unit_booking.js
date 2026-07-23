// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Unit Booking", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status === "Reserved") {
			frm.add_custom_button(__("Convert to Lease"), () => {
				frappe.call({
					method: "bunood_realestate.real_estate.doctype.unit_booking.unit_booking.convert_to_lease",
					args: { booking: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating draft lease..."),
					callback: (r) => {
						if (r.message && r.message.lease) {
							frappe.set_route("Form", "Lease Contract", r.message.lease);
						}
					},
				});
			});
		}
		if (frm.doc.docstatus === 1 && frm.doc.lease_contract) {
			frm.add_custom_button(__("Open Lease"), () => frappe.set_route("Form", "Lease Contract", frm.doc.lease_contract));
		}
	},
});
