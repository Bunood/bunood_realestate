// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// Operations Center — the installment JOURNEY on the Lease Contract form
// (docs/plan-invoicing-automation.md §2/§3). Every row shows its live health (computed
// server-side from the GL, never a stored flag) and the ONE action that moves it
// forward. The operator sees the whole cycle, not a bare "issue" button.

window.bnd_ops = window.bnd_ops || {};

// key → colour + Arabic-first label + the single contextual action.
bnd_ops.HEALTH = {
	planned: { dot: "#9CA3AF", label: () => __("Planned"), action: "issue" },
	due_soon: { dot: "#F59E0B", label: (d) => __("Due in {0} day(s)", [Math.abs(d ?? 0)]), action: "issue" },
	overdue_unissued: { dot: "#DC2626", label: (d) => __("Overdue {0} day(s)", [d ?? 0]), action: "issue" },
	// Legacy rows from the old draft-invoice policy: a draft is neither a tax document
	// nor a receivable, so it gets its own state instead of masquerading as issued.
	draft_invoice: { dot: "#6366F1", label: () => __("Draft invoice — not issued"), action: "view_invoice" },
	issued: { dot: "#EAB308", label: () => __("Invoiced"), action: "receive" },
	// Cash recorded, receipt awaiting approval — never dunned, never fined.
	pending_receipt: { dot: "#0EA5E9", label: () => __("Receipt awaiting approval"), action: "view_payment" },
	partially_paid: { dot: "#EAB308", label: () => __("Partially paid"), action: "receive" },
	overdue_unpaid: { dot: "#DC2626", label: (d) => __("Overdue {0} day(s)", [d ?? 0]), action: "receive" },
	paid: { dot: "#16A34A", label: () => __("Paid"), action: "view_payment" },
	cancelled: { dot: "#6B7280", label: () => __("Cancelled"), action: "view_invoice" },
	failed: { dot: "#DC2626", label: () => __("Failed"), action: "view_invoice" },
};

bnd_ops.render = function (frm) {
	if (frm.is_new() || frm.doc.docstatus !== 1) return;
	// `refresh` fires on every save/reload and add_section APPENDS — without this the
	// panel stacks up copy after copy (and each copy rebinds its click handlers).
	frm.dashboard.wrapper.find("[data-bnd-ops-panel]").closest(".form-dashboard-section").remove();
	frappe.call({
		method: "bunood_realestate.real_estate.operations.get_installments",
		args: { lease_contract: frm.doc.name },
		callback(r) {
			if (r.message) bnd_ops.paint(frm, r.message);
		},
	});
};

bnd_ops.paint = function (frm, data) {
	const rows = data.installments || [];
	if (!rows.length) return;
	// Use the LEASE's company currency (the server resolved it) — not the system default,
	// which would mislabel amounts on a multi-company site.
	const money = (v) => format_currency(v, data.currency);

	const body = rows
		.map((row) => {
			const h = bnd_ops.HEALTH[row.health.key] || bnd_ops.HEALTH.planned;
			const amount = row.grand_total || row.base_amount;
			const paidNote =
				row.health.key === "partially_paid"
					? `<div class="text-muted small">${__("Remaining")}: ${money(row.outstanding)}</div>`
					: "";
			return `<tr>
				<td>${frappe.datetime.str_to_user(row.due_date)}</td>
				<td>${__("Period")} ${row.period_no || ""}</td>
				<td style="text-align:end">${money(amount)}${paidNote}</td>
				<td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${h.dot};margin-inline-end:6px;"></span>${h.label(row.health.days)}</td>
				<td>${bnd_ops.invoiceLink(row)}</td>
				<td style="text-align:end">${bnd_ops.actionButton(row, h, data)}</td>
			</tr>`;
		})
		.join("");

	const policyNote = data.issue_on_payment
		? `<div class="text-muted small" style="margin-bottom:8px;">${__(
				"Policy: the invoice is created when you record the payment."
		  )}</div>`
		: "";

	const html = `<div data-bnd-ops-panel="1">${policyNote}<div style="overflow-x:auto;"><table class="table table-sm" style="margin:0;">
		<thead><tr>
			<th>${__("Due Date")}</th><th>${__("Period")}</th>
			<th style="text-align:end">${__("Amount")}</th>
			<th>${__("Status")}</th><th>${__("Invoice")}</th>
			<th style="text-align:end">${__("Action")}</th>
		</tr></thead><tbody>${body}</tbody></table></div></div>`;

	frm.dashboard.add_section(html, __("Operations Center — Installments"));
	// Scope the binding to THIS panel so a stale panel's buttons can never act.
	frm.dashboard.wrapper.find("[data-bnd-ops-panel] [data-bnd-action]").on("click", function () {
		const el = $(this);
		bnd_ops.run(frm, el.attr("data-bnd-action"), el.attr("data-schedule"), el.attr("data-invoice"));
	});
};

bnd_ops.invoiceLink = function (row) {
	if (!row.sales_invoice) return `<span class="text-muted">—</span>`;
	return `<a href="/app/sales-invoice/${encodeURIComponent(row.sales_invoice)}">${frappe.utils.escape_html(
		row.sales_invoice
	)}</a>`;
};

bnd_ops.actionButton = function (row, h, data) {
	const attrs = `data-schedule="${frappe.utils.escape_html(row.name)}" data-invoice="${frappe.utils.escape_html(
		row.sales_invoice || ""
	)}"`;
	const btn = (action, label, kind) =>
		`<button class="btn btn-xs ${kind}" data-bnd-action="${action}" ${attrs}>${label}</button>`;

	switch (h.action) {
		case "issue":
			// Under the On-Payment policy the invoice is born at collection time, so the
			// operator is offered the action they actually perform: receiving money.
			return data.issue_on_payment
				? btn("receive", __("Receive Payment"), "btn-primary")
				: btn("issue", __("Issue Invoice"), "btn-primary");
		case "receive":
			return btn("receive", __("Receive Payment"), "btn-primary");
		case "view_payment":
			return btn("view_payment", __("View Receipt"), "btn-default");
		default:
			return row.sales_invoice ? btn("view_invoice", __("View"), "btn-default") : "";
	}
};

bnd_ops.run = function (frm, action, schedule, invoice) {
	if (action === "issue") return bnd_ops.issue(frm, schedule);
	if (action === "receive") return bnd_ops.receiveDialog(frm, schedule, invoice);
	if (action === "view_invoice") return frappe.set_route("Form", "Sales Invoice", invoice);
	if (action === "view_payment") return bnd_ops.viewPayment(invoice);
};

bnd_ops.issue = function (frm, schedule) {
	frappe.confirm(
		__(
			"Issue the tax invoice for this installment?<br><br>Once submitted it is reported to ZATCA and can no longer be cancelled — only corrected with a credit note."
		),
		() => {
			frappe.call({
				method: "bunood_realestate.real_estate.operations.issue_invoice",
				args: { schedule },
				freeze: true,
				freeze_message: __("Issuing invoice..."),
				callback(r) {
					if (!r.message) return;
					// Honour `created`: the server returns an EXISTING invoice when the row
					// was already invoiced, and claiming "issued" there would be a lie.
					frappe.show_alert(
						r.message.created
							? { message: __("Invoice {0} issued", [r.message.sales_invoice]), indicator: "green" }
							: { message: __("Already invoiced: {0}", [r.message.sales_invoice]), indicator: "blue" }
					);
					frm.reload_doc();
				},
			});
		}
	);
};

bnd_ops.receiveDialog = function (frm, schedule, invoice) {
	const d = new frappe.ui.Dialog({
		title: __("Receive Payment"),
		fields: [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: invoice
					? ""
					: `<div class="alert alert-warning">${__(
							"No invoice exists yet — it will be issued first, then the receipt recorded."
					  )}</div>`,
			},
			{ fieldname: "mode_of_payment", fieldtype: "Link", options: "Mode of Payment", label: __("How was it received?"), reqd: 1 },
			{
				fieldname: "paid_to",
				fieldtype: "Link",
				options: "Account",
				label: __("Deposited to (Bank / Cash)"),
				get_query: () => ({
					filters: { company: frm.doc.company, account_type: ["in", ["Bank", "Cash"]], is_group: 0 },
				}),
			},
			{ fieldname: "cb", fieldtype: "Column Break" },
			{ fieldname: "amount", fieldtype: "Currency", label: __("Amount Received"), description: __("Leave blank for the full outstanding amount.") },
			{ fieldname: "posting_date", fieldtype: "Date", label: __("Received On"), default: frappe.datetime.get_today() },
			{ fieldname: "sec", fieldtype: "Section Break", label: __("Transfer / Cheque details") },
			{ fieldname: "reference_no", fieldtype: "Data", label: __("Reference No") },
			{ fieldname: "reference_date", fieldtype: "Date", label: __("Reference Date") },
			{ fieldname: "receipt_file", fieldtype: "Attach", label: __("Receipt") },
		],
		primary_action_label: __("Record Receipt"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "bunood_realestate.real_estate.operations.receive_payment",
				args: { schedule, sales_invoice: invoice || null, ...values },
				freeze: true,
				freeze_message: __("Recording receipt..."),
				callback(r) {
					if (!r.message) return;
					frappe.show_alert({
						message: __("Receipt prepared — review and submit"),
						indicator: "blue",
					});
					frappe.set_route("Form", "Payment Entry", r.message.payment_entry);
				},
			});
		},
	});
	d.show();
};

bnd_ops.viewPayment = function (invoice) {
	// The receipt is whatever settled this invoice — read it from ERPNext's own link
	// table rather than storing our own pointer.
	frappe.db
		.get_list("Payment Entry Reference", {
			filters: { reference_doctype: "Sales Invoice", reference_name: invoice, docstatus: 1 },
			fields: ["parent"],
			limit: 1,
			parent: "Payment Entry",
		})
		.then((rows) => {
			if (rows && rows.length) frappe.set_route("Form", "Payment Entry", rows[0].parent);
			else frappe.set_route("Form", "Sales Invoice", invoice);
		});
};

frappe.ui.form.on("Lease Contract", {
	refresh(frm) {
		bnd_ops.render(frm);
	},
});
