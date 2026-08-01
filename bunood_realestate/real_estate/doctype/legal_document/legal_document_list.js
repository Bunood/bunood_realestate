// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// The register work-list: colour each row by how close it is to expiry, and give operators a
// one-click "Run expiry alerts now" (role-gated on the server) mirroring the lease-renewal button.

frappe.listview_settings["Legal Document"] = {
	add_fields: ["is_perpetual", "expiry_date", "status"],

	get_indicator(doc) {
		if (doc.status === "Cancelled") return [__("Cancelled"), "gray", "status,=,Cancelled"];
		if (doc.status === "Superseded") return [__("Superseded"), "gray", "status,=,Superseded"];
		if (doc.is_perpetual) return [__("Perpetual"), "blue", "is_perpetual,=,1"];
		if (!doc.expiry_date) return [__("No Expiry"), "orange"];
		const days = frappe.datetime.get_day_diff(doc.expiry_date, frappe.datetime.get_today());
		if (days < 0) return [__("Expired"), "red", "expiry_date,<,Today"];
		if (days <= 30) return [__("Due Soon"), "orange"];
		return [__("OK"), "green"];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Run Expiry Alerts Now"), () => {
			frappe.call({
				method: "bunood_realestate.real_estate.notifications.run_document_expiry_alerts_now",
				freeze: true,
				freeze_message: __("Scanning documents for expiry…"),
				callback: (r) => {
					frappe.show_alert({
						message: __("{0} document reminder(s) sent.", [r.message || 0]),
						indicator: "green",
					});
				},
			});
		});
	},
};
