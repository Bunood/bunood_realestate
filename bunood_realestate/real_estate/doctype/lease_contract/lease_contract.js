// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Lease Contract", {
	onload(frm) {
		// Single creation path: a brand-new lease goes through the guided wizard.
		// Skip amendments (they carry amended_from and must keep the doctype form).
		if (frm.is_new() && !frm.doc.amended_from) {
			frappe.set_route("new-lease");
		}
	},

	refresh(frm) {
		recompute_annual_rent(frm);

		// Prominent top button: preview the live data before printing / acting.
		if (!frm.is_new()) {
			const btn = frm.add_custom_button(__("Preview"), () => lease_preview_dialog(frm));
			if (btn) btn.removeClass("btn-default").addClass("btn-primary");
		}

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(
				__("Generate Due Invoices"),
				() => {
					frappe.call({
						method: "bunood_realestate.real_estate.tasks.generate_now",
						args: { lease_contract: frm.doc.name },
						freeze: true,
						freeze_message: __("Generating invoices..."),
						callback: (r) => {
							frappe.show_alert({
								message: __("Created {0} invoice(s)", [r.message || 0]),
								indicator: "green",
							});
						},
					});
				},
				__("Rent")
			);

			frm.add_custom_button(
				__("Post Fee Charges"),
				() => {
					frappe.call({
						method: "bunood_realestate.core.charge.post_reference_charges",
						args: { reference_doctype: "Lease Contract", reference_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Posting fee charges..."),
						callback: (r) => {
							const inv = r.message || [];
							frappe.show_alert({
								message: inv.length
									? __("Created invoice(s): {0}", [inv.join(", ")])
									: __("No pending fee charges"),
								indicator: inv.length ? "green" : "orange",
							});
						},
					});
				},
				__("Rent")
			);

			if (!frm.doc.deposit_received) {
				frm.add_custom_button(__("Record Deposit"), () => deposit_dialog(frm, "receive"), __("Deposit"));
			} else if (flt(frm.doc.deposit_refunded) < flt(frm.doc.deposit_received)) {
				frm.add_custom_button(__("Refund Deposit"), () => deposit_dialog(frm, "refund"), __("Deposit"));
			}

			if (["Active", "Expired"].includes(frm.doc.status)) {
				frm.add_custom_button(__("Renew"), () => renew_dialog(frm), __("Lifecycle"));
			}
			if (frm.doc.status === "Active") {
				frm.add_custom_button(
					__("Terminate"),
					() => frappe.new_doc("Lease Termination", { lease_contract: frm.doc.name }),
					__("Lifecycle")
				);
			}

			frm.add_custom_button(
				__("WhatsApp Dues"),
				() => {
					frappe.call({
						method: "bunood_realestate.real_estate.collections.dues_whatsapp_link",
						args: { lease_contract: frm.doc.name },
						freeze: true,
						callback: (r) => {
							if (r.message && r.message.link) {
								window.open(r.message.link, "_blank");
							}
						},
					});
				},
				__("Collections")
			);

			frm.add_custom_button(
				__("Post Late Fees"),
				() => {
					frappe.call({
						method: "bunood_realestate.real_estate.collections.run_late_fees_now",
						args: { lease_contract: frm.doc.name },
						freeze: true,
						freeze_message: __("Posting late fees..."),
						callback: (r) => {
							frappe.show_alert({
								message: __("Charged {0} late fee(s)", [r.message || 0]),
								indicator: (r.message || 0) ? "green" : "orange",
							});
						},
					});
				},
				__("Collections")
			);
		}
	},
});

function lease_preview_dialog(frm) {
	frappe.call({
		method: "bunood_realestate.real_estate.previews.lease_preview",
		args: { lease_contract: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading..."),
		callback: (r) => {
			if (!r.message) return;
			const d = r.message;
			const esc = frappe.utils.escape_html;
			const money = (v) => frappe.format(flt(v), { fieldtype: "Currency" }, { currency: d.currency });
			const chip = (label, value, color) =>
				`<div style="flex:1;min-width:120px;background:#f7f9f8;border:1px solid #e8eae7;border-radius:12px;padding:10px 12px;">
					<div style="font-size:11.5px;color:#6b7280;">${esc(label)}</div>
					<div style="font-size:17px;font-weight:700;color:${color || "#1F5145"};">${value}</div></div>`;
			let unitRows = (d.units || [])
				.map((u) => `<tr><td style="padding:4px 8px;">${esc(u.unit || "-")}</td>
					<td style="padding:4px 8px;text-align:end;">${money(u.annual_rent)}</td></tr>`)
				.join("");
			if (!unitRows) unitRows = `<tr><td colspan="2" style="padding:4px 8px;color:#9ca3af;">${__("No units")}</td></tr>`;
			const sched = Object.entries(d.schedule || {})
				.map(([k, v]) => `<span style="display:inline-block;background:#eef2f1;color:#1F5145;border-radius:20px;padding:2px 10px;margin:2px;font-size:12px;">${esc(__(k))}: ${v}</span>`)
				.join("") || `<span style="color:#9ca3af;">${__("No schedule yet")}</span>`;
			const html = `
				<div dir="auto" style="font-size:13.5px;">
					<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
						${chip(__("Tenant"), esc(d.tenant_name))}
						${chip(__("Property"), esc(d.property || "-"))}
						${chip(__("Status"), esc(__(d.status || "")))}
					</div>
					<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
						${chip(__("Annual Rent"), money(d.annual_rent_total))}
						${chip(__("Deposit Held"), money(d.deposit_held), "#C8923C")}
						${chip(__("Outstanding"), money(d.outstanding), flt(d.outstanding) > 0 ? "#DC2626" : "#1F5145")}
					</div>
					<div style="margin:8px 0;color:#374151;">
						<b>${__("Term")}:</b> ${esc(d.start_date || "")} → ${esc(d.end_date || "")}
						${d.hijri_start_date ? `<span style="color:#6b7280;"> · ${__("Hijri")}: ${esc(d.hijri_start_date)} → ${esc(d.hijri_end_date || "")}</span>` : ""}
					</div>
					<table style="width:100%;border-collapse:collapse;margin:8px 0;">
						<thead><tr style="border-bottom:1px solid #e5e7eb;">
							<th style="padding:4px 8px;text-align:start;">${__("Unit")}</th>
							<th style="padding:4px 8px;text-align:end;">${__("Annual Rent")}</th></tr></thead>
						<tbody>${unitRows}</tbody>
					</table>
					<div style="margin-top:8px;"><b>${__("Rent Schedule")}:</b><br>${sched}</div>
				</div>`;
			const dlg = new frappe.ui.Dialog({
				title: __("Lease Preview — {0}", [d.name]),
				size: "large",
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

function renew_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Renew Lease"),
		fields: [
			{ fieldname: "rent_bump_pct", fieldtype: "Percent", label: __("Rent Increase %"), default: 0 },
			{ fieldname: "months", fieldtype: "Int", label: __("Duration in months (blank = same as current)") },
		],
		primary_action_label: __("Create Renewal"),
		primary_action(v) {
			frappe.call({
				method: "bunood_realestate.real_estate.doctype.lease_contract.lease_contract.renew_lease",
				args: { lease_contract: frm.doc.name, rent_bump_pct: v.rent_bump_pct, months: v.months },
				freeze: true,
				callback: (r) => {
					if (r.message) frappe.set_route("Form", "Lease Contract", r.message);
				},
			});
			d.hide();
		},
	});
	d.show();
}

function deposit_dialog(frm, mode) {
	const is_receive = mode === "receive";
	const account_field = is_receive ? "paid_to_account" : "paid_from_account";
	const held = flt(frm.doc.deposit_received) - flt(frm.doc.deposit_refunded);
	const d = new frappe.ui.Dialog({
		title: is_receive ? __("Record Security Deposit") : __("Refund Security Deposit"),
		fields: [
			{
				fieldname: "amount",
				fieldtype: "Currency",
				label: __("Amount"),
				reqd: 1,
				default: is_receive ? frm.doc.deposit_amount : held,
			},
			{
				fieldname: account_field,
				fieldtype: "Link",
				label: is_receive ? __("Received Into (Bank/Cash)") : __("Refunded From (Bank/Cash)"),
				options: "Account",
				reqd: 1,
				get_query: () => ({
					filters: { company: frm.doc.company, is_group: 0, account_type: ["in", ["Bank", "Cash"]] },
				}),
			},
			{ fieldname: "posting_date", fieldtype: "Date", label: __("Date"), default: frappe.datetime.get_today() },
		],
		primary_action_label: is_receive ? __("Record") : __("Refund"),
		primary_action(v) {
			frappe.call({
				method: is_receive
					? "bunood_realestate.real_estate.deposits.record_deposit"
					: "bunood_realestate.real_estate.deposits.refund_deposit",
				args: {
					lease_contract: frm.doc.name,
					amount: v.amount,
					[account_field]: v[account_field],
					posting_date: v.posting_date,
				},
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Done"), indicator: "green" });
					frm.reload_doc();
				},
			});
			d.hide();
		},
	});
	d.show();
}

frappe.ui.form.on("Lease Unit", {
	annual_rent(frm) {
		recompute_annual_rent(frm);
	},
	units_remove(frm) {
		recompute_annual_rent(frm);
	},
});

function recompute_annual_rent(frm) {
	let total = 0;
	(frm.doc.units || []).forEach((row) => {
		total += flt(row.annual_rent);
	});
	frm.set_value("annual_rent_total", total);
}
