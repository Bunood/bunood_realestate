// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// Financial Snapshot (plan-financial-reporting.md Phase 2): live month figures inside
// the Unit form — revenue, expenses, net, arrears, current lease — so the user gets the
// answer without opening a report. Deep-dive stays one click away: the Unit Statement.

frappe.ui.form.on("Real Estate Unit", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Unit Statement"), () => {
			frappe.set_route("query-report", "Unit Statement", { unit: frm.doc.name });
		});

		frappe.call({
			method: "bunood_realestate.real_estate.snapshot.unit_snapshot",
			args: { unit: frm.doc.name },
			callback(r) {
				if (r.message) bnd_render_unit_snapshot(frm, r.message);
			},
		});
	},
});

function bnd_render_unit_snapshot(frm, s) {
	const money = (v) => frappe.format(v, { fieldtype: "Currency" });
	const row = (label, value, color) =>
		`<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border-color);">
			<span>${label}</span><span style="font-weight:600;${color ? `color:${color};` : ""}">${value}</span>
		</div>`;

	const lease_line = s.lease
		? `${frappe.utils.escape_html(s.tenant || "")} — <a href="/app/lease-contract/${encodeURIComponent(s.lease)}">${frappe.utils.escape_html(s.lease)}</a> (${__("until")} ${frappe.datetime.str_to_user(s.lease_end)})`
		: __("No active lease");

	const html =
		row(__("Revenue (this month)"), money(s.month_revenue)) +
		row(__("Expenses (this month)"), money(s.month_expense)) +
		row(__("Net (this month)"), money(s.month_net), s.month_net >= 0 ? "var(--bnd-success,#16A34A)" : "var(--bnd-danger,#DC2626)") +
		row(__("Arrears"), money(s.arrears), s.arrears > 0 ? "var(--bnd-danger,#DC2626)" : undefined) +
		row(__("Current Lease"), lease_line);

	frm.dashboard.add_section(html, __("Financial Snapshot"));
}
