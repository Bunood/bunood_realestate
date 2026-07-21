// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Property", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Create Units"),
			() => open_bulk_units_dialog(frm),
			__("Units")
		);
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
