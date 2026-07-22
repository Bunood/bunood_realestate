// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.ui.form.on("Lease Contract", {
	refresh(frm) {
		recompute_annual_rent(frm);

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
		}
	},
});

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
