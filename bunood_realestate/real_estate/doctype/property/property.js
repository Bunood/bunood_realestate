// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Property", {
	onload(frm) {
		// Single creation path: every "New Property" goes through the guided wizard,
		// never the raw doctype form. The plain form remains only for viewing/editing
		// an existing property. (The wizard creates via a server method and routes to
		// the saved doc, so this never loops.)
		if (frm.is_new()) {
			frappe.set_route("new-property");
		}
	},

	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Create Units"),
			() => open_bulk_units_dialog(frm),
			__("Units")
		);

		if (frm.doc.management_behavior === "managed") {
			frm.add_custom_button(
				__("Owner Payout"),
				() => {
					const d = new frappe.ui.Dialog({
						title: __("Owner Payout"),
						fields: [
							{ fieldname: "from_date", fieldtype: "Date", label: __("From"), reqd: 1 },
							{ fieldname: "to_date", fieldtype: "Date", label: __("To"), reqd: 1, default: frappe.datetime.get_today() },
						],
						primary_action_label: __("Post Payout"),
						primary_action(v) {
							frappe.call({
								method: "bunood_realestate.real_estate.management.generate_owner_payout",
								args: { property: frm.doc.name, from_date: v.from_date, to_date: v.to_date },
								freeze: true,
								callback: (r) => {
									if (r.message) {
										frappe.show_alert({
											message: __("Owner payout {0} posted ({1})", [
												format_currency(r.message.owner_payout),
												r.message.journal_entry,
											]),
											indicator: "green",
										});
									}
								},
							});
							d.hide();
						},
					});
					d.show();
				},
				__("Owner")
			);
		}

		if (frm.doc.management_behavior === "master_lease") {
			frm.add_custom_button(
				__("Generate Head-Lease Schedule"),
				() => {
					frappe.call({
						method: "bunood_realestate.real_estate.head_lease.generate_now",
						args: { property: frm.doc.name },
						freeze: true,
						callback: (r) => {
							frappe.show_alert({
								message: __("Created {0} period(s)", [r.message || 0]),
								indicator: "green",
							});
						},
					});
				},
				__("Head Lease")
			);
			frm.add_custom_button(
				__("Post Due Head-Lease Bills"),
				() => {
					frappe.call({
						method: "bunood_realestate.real_estate.head_lease.post_due_bills",
						args: { property: frm.doc.name },
						freeze: true,
						freeze_message: __("Posting bills..."),
						callback: (r) => {
							frappe.show_alert({
								message: __("Created {0} purchase invoice(s)", [r.message || 0]),
								indicator: "green",
							});
						},
					});
				},
				__("Head Lease")
			);
		}
	},
});

function open_bulk_units_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Bulk Create Units"),
		fields: [
			{ fieldname: "count", fieldtype: "Int", label: __("Number of Units"), reqd: 1, default: 1 },
			{ fieldname: "start", fieldtype: "Int", label: __("Start Number"), default: 1 },
			{ fieldname: "prefix", fieldtype: "Data", label: __("Unit No Prefix"), default: "" },
			{
				fieldname: "unit_type",
				fieldtype: "Select",
				label: __("Unit Type"),
				options: ["", "Apartment", "Shop", "Office", "Villa", "Warehouse", "Other"].join("\n"),
			},
			{ fieldname: "floor", fieldtype: "Data", label: __("Floor") },
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			frappe.call({
				method: "bunood_realestate.real_estate.doctype.property.property.create_units",
				args: { property: frm.doc.name, ...values },
				freeze: true,
				freeze_message: __("Creating units..."),
				callback: (r) => {
					const n = (r.message || []).length;
					frappe.show_alert({ message: __("Created {0} unit(s)", [n]), indicator: "green" });
					frm.reload_doc();
				},
			});
			d.hide();
		},
	});
	d.show();
}
