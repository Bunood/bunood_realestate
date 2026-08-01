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

		// Prominent top buttons — preview live data before printing, and jump to finance.
		const pbtn = frm.add_custom_button(__("Preview"), () => property_preview_dialog(frm));
		if (pbtn) pbtn.removeClass("btn-default").addClass("btn-primary");
		frm.add_custom_button(__("Building View"), () => {
			frappe.set_route("property-building").then(() => {
				const pb = frappe.pages["property-building"];
				if (pb && pb.bnd_set_property) pb.bnd_set_property(frm.doc.name);
			});
		});
		frm.add_custom_button(__("Finance"), () => {
			frappe.set_route("property-finance").then(() => {
				const pf = frappe.pages["property-finance"];
				if (pf && pf.bnd_set_property) pf.bnd_set_property(frm.doc.name);
			});
		});

		frm.add_custom_button(
			__("Create Units"),
			() => open_bulk_units_dialog(frm),
			__("Units")
		);

		// CAM: materialize due service-charge periods now (the charge generator then bills
		// them). Only surfaced once the property actually defines a service charge.
		if ((frm.doc.service_charges || []).length) {
			frm.add_custom_button(
				__("Generate CAM"),
				() => {
					frappe.call({
						method: "bunood_realestate.real_estate.cam.generate_cam_now",
						args: { property: frm.doc.name },
						freeze: true,
						freeze_message: __("Materializing service-charge periods…"),
						callback: (r) => {
							frappe.show_alert({
								message: __("{0} CAM period line(s) materialized.", [r.message || 0]),
								indicator: "green",
							});
						},
					});
				},
				__("Units")
			);
		}

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

function property_preview_dialog(frm) {
	frappe.call({
		method: "bunood_realestate.real_estate.previews.property_preview",
		args: { property: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading..."),
		callback: (r) => {
			if (!r.message) return;
			const d = r.message;
			const esc = frappe.utils.escape_html;
			const chip = (label, value, color) =>
				`<div style="flex:1;min-width:120px;background:#f7f9f8;border:1px solid #e8eae7;border-radius:12px;padding:12px 14px;">
					<div style="font-size:11.5px;color:#6b7280;">${esc(label)}</div>
					<div style="font-size:20px;font-weight:800;color:${color || "#1F5145"};">${value}</div></div>`;
			const html = `
				<div dir="auto">
					<h4 style="margin:0 0 10px;">${esc(d.property_name)}</h4>
					<div style="display:flex;gap:10px;flex-wrap:wrap;">
						${chip(__("Occupancy"), `${d.occupancy_pct}%`, "#1F5145")}
						${chip(__("Units"), d.units_total, "#475569")}
						${chip(__("Occupied"), d.occupied, "#2D6F5E")}
						${chip(__("Vacant"), d.vacant, "#C8923C")}
						${chip(__("Active Leases"), d.active_leases, "#475569")}
					</div>
				</div>`;
			const dlg = new frappe.ui.Dialog({
				title: __("Property Preview — {0}", [d.name]),
				fields: [{ fieldtype: "HTML", fieldname: "body", options: html }],
				primary_action_label: __("Print"),
				primary_action() {
					dlg.hide();
					frm.print_doc();
				},
			});
			dlg.show();
		},
	});
}

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

// ---------------------------------------------------------------------------
// Wrapper reports (plan-financial-reporting.md Phase 3): "Property Profitability"
// IS ERPNext's P&L + the Property dimension filter — a preset entry, not new SQL.
// Same for the General Ledger. Appended as a second handler block so the main
// form logic above stays untouched.
// ---------------------------------------------------------------------------
frappe.ui.form.on("Property", {
	refresh(frm) {
		if (frm.is_new()) return;
		const route = (report, extra) =>
			frappe.set_route(
				"query-report",
				report,
				Object.assign({ company: frm.doc.company, property: frm.doc.name }, extra || {})
			);

		frm.add_custom_button(
			__("Profit & Loss (Property)"),
			() =>
				route("Profit and Loss Statement", {
					filter_based_on: "Date Range",
					period_start_date: frappe.datetime.year_start(),
					period_end_date: frappe.datetime.get_today(),
					periodicity: "Yearly",
				}),
			__("Reports")
		);
		frm.add_custom_button(
			__("General Ledger (Property)"),
			() =>
				route("General Ledger", {
					from_date: frappe.datetime.year_start(),
					to_date: frappe.datetime.get_today(),
					group_by: "Group by Voucher (Consolidated)",
				}),
			__("Reports")
		);
		if (frm.doc.owner_party) {
			frm.add_custom_button(
				__("Owner Ledger"),
				() =>
					frappe.set_route("query-report", "Owner Ledger", {
						owner: frm.doc.owner_party,
						property: frm.doc.name,
					}),
				__("Reports")
			);
		}
	},
});
