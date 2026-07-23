// Copyright (c) 2026, Bunood and contributors
// Import Leases — upload an .xlsx of lease rows; each becomes a DRAFT Lease Contract
// via the same validated server logic the wizard uses (no duplicate import path).

frappe.pages["lease-import"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Import Leases"), single_column: true });
	const $c = $(wrapper).find(".layout-main-section");
	const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));

	const cols = ["tenant_name", "tenant_phone", "unit", "contract_type", "start_date", "end_date", "billing_cycle", "annual_rent", "deposit"];

	$c.html(
		'<div style="max-width:900px;margin:0 auto;padding:8px 4px 40px;">' +
		'<div style="background:linear-gradient(120deg,var(--bnd-primary,#1F5145),var(--bnd-primary-700,#12352C));color:#fff;border-radius:16px;padding:22px 26px;">' +
		'<div style="font-size:22px;font-weight:800;">' + esc(__("Import Lease Contracts")) + "</div>" +
		'<div style="opacity:.85;margin-top:4px;">' + esc(__("Upload an Excel (.xlsx). Each row creates a Draft lease you can review then activate.")) + "</div></div>" +
		'<div style="background:#fff;border:1px solid var(--bnd-border,#DCE6E2);border-radius:14px;padding:20px;margin-top:16px;">' +
		"<p><b>" + esc(__("Required columns (first row = header):")) + "</b></p>" +
		'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px;">' +
		cols.map((k) => '<span style="background:var(--bnd-surface-2,#EEF3F1);border-radius:6px;padding:3px 9px;font-family:monospace;font-size:12px;">' + k + "</span>").join("") +
		"</div>" +
		'<p class="text-muted" style="font-size:12px;">' + esc(__("unit = the unit's ID; contract_type = Residential/Commercial (سكني/تجاري); dates = YYYY-MM-DD; billing_cycle = Monthly/Quarterly/Semi-Annual/Annual. tenant_name + unit are required.")) + "</p>" +
		'<button class="btn btn-default btn-sm" id="li-template">' + esc(__("Download Template")) + "</button> " +
		'<button class="btn btn-primary" id="li-upload">' + esc(__("Upload & Import")) + "</button>" +
		'<div id="li-result" style="margin-top:16px;"></div></div></div>'
	);

	$c.find("#li-template").on("click", () => {
		const csv = cols.join(",") + "\n" + "أحمد المالكي,0551234567,<unit-id>,Residential,2026-01-01,2026-12-31,Monthly,60000,5000\n";
		const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = "lease_import_template.csv";
		a.click();
	});

	$c.find("#li-upload").on("click", () => {
		new frappe.ui.FileUploader({
			allow_multiple: false,
			restrictions: { allowed_file_types: [".xlsx"] },
			on_success: (file_doc) => runImport(file_doc.file_url),
		});
	});

	function runImport(file_url) {
		const $r = $c.find("#li-result");
		$r.html('<span class="text-muted">' + __("Importing…") + "</span>");
		frappe.call({
			method: "bunood_realestate.real_estate.doctype.lease_contract.lease_contract.import_leases",
			args: { file_url },
			freeze: true,
			freeze_message: __("Importing leases..."),
			callback: (res) => {
				const m = res.message || { created: [], errors: [] };
				let html =
					'<div class="alert alert-success">' + __("Created {0} draft lease(s).", [m.created.length]) + "</div>";
				if (m.errors && m.errors.length) {
					html += '<div class="alert alert-warning">' + __("{0} row(s) failed:", [m.errors.length]) + "</div>";
					html += '<table class="table table-bordered"><thead><tr><th>' + esc(__("Row")) + "</th><th>" + esc(__("Error")) + "</th></tr></thead><tbody>" +
						m.errors.map((e) => "<tr><td>" + esc(e.row) + "</td><td>" + esc(e.error) + "</td></tr>").join("") + "</tbody></table>";
				}
				$r.html(html);
			},
			error: () => $r.html('<div class="alert alert-danger">' + __("Import failed.") + "</div>"),
		});
	}
};
