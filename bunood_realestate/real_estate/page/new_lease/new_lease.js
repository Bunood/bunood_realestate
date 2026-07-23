// Copyright (c) 2026, Bunood and contributors
// New Lease wizard — a guided 7-step Ejar-style contract creation flow that
// builds (and optionally activates) a Lease Contract + its units atomically.
// Single creation path; the plain doctype form redirects here.

frappe.pages["new-lease"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("New Lease"), single_column: true });
	injectStyles();
	const $root = $(wrapper).find(".layout-main-section").addClass("np-wrap");

	const CYCLE = { Monthly: 12, Quarterly: 4, "Semi-Annual": 2, Annual: 1 };
	const S = {
		step: 1,
		available: [],
		picker: { property: "", unit: "", annual_rent: "", deposit: "" },
		units: [],
		publish: false,
		c: {
			contract_type: "residential", contract_subtype: "New", ejar_contract_no: "",
			tenant_name: "", tenant_phone: "",
			start_date: frappe.datetime.get_today(), end_date: "",
			hijri_start_date: "", hijri_end_date: "", sealing_date: "",
			billing_cycle: "Monthly", payment_day: "", retainer_fee: "", security_deposit_extra: "", payment_methods_text: "",
			lessor_org_type: "Individual", lessor_company_name: "", lessor_cr_number: "", lessor_unified_number: "", lessor_vat_number: "",
			tenant_org_type: "Individual", tenant_company_name: "", tenant_cr_number: "", tenant_unified_number: "", tenant_vat_number: "",
			guarantor_name: "", guarantor_id_number: "", guarantor_phone: "",
			broker_company_name: "", broker_cr_number: "", broker_employee_name: "",
			deed_number: "", deed_type: "", deed_issuer: "", deed_issue_date: "",
			business_name: "", business_cr_number: "", isic_activity: "", license_number: "",
			brokerage_fee: "", general_services_amount: "", waste_removal_fee: "", engineering_supervision_fee: "", unit_finishing_fee: "",
			electricity_annual: "", water_annual: "", gas_annual: "", parking_annual: "", parking_lots_rented: "",
			lessor_obligations: "", tenant_obligations: "", additional_terms: "",
		},
	};

	$root.html('<div class="np"><div class="np-empty">' + __("Loading…") + "</div></div>");
	frappe
		.call({ method: "bunood_realestate.real_estate.doctype.lease_contract.lease_contract.available_units" })
		.then((r) => { S.available = r.message || []; render(); })
		.catch(() => { S.available = []; render(); });

	// ---- helpers -----------------------------------------------------------
	const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));
	const isCommercial = () => S.c.contract_type === "commercial";
	const money = (v) => frappe.format(v || 0, { fieldtype: "Currency" });

	function totals() {
		const annual = S.units.reduce((s, u) => s + (parseFloat(u.annual_rent) || 0), 0);
		const inst = CYCLE[S.c.billing_cycle] || 12;
		const per = annual / inst;
		const rate = isCommercial() ? 15 : 0;
		return { annual, inst, per, rate, vat: (annual * rate) / 100, total: annual + (annual * rate) / 100 };
	}

	function field(label, key, opts) {
		opts = opts || {};
		const v = S.c[key] == null ? "" : S.c[key];
		const req = opts.req ? ' <span class="np-req">*</span>' : "";
		const ph = opts.ph ? ' placeholder="' + esc(opts.ph) + '"' : "";
		return '<div class="np-f"><label>' + esc(label) + req + '</label><input type="' + (opts.type || "text") + '" data-k="' + key + '" value="' + esc(v) + '"' + ph + "></div>";
	}
	function sel(label, key, options) {
		const v = S.c[key];
		return '<div class="np-f"><label>' + esc(label) + '</label><select data-k="' + key + '">' +
			options.map((o) => '<option value="' + esc(o[0]) + '"' + (o[0] === v ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") + "</select></div>";
	}
	function area(label, key) {
		return '<div class="np-f"><label>' + esc(label) + '</label><textarea data-k="' + key + '" rows="2">' + esc(S.c[key]) + "</textarea></div>";
	}

	// ---- render ------------------------------------------------------------
	function render() {
		const steps = [__("Contract Type"), __("Lessor"), __("Tenant"), __("Broker"), __("Deed & Activity"), __("Financials"), __("Review & Publish")];
		$root.html(
			'<div class="np">' +
			'<div class="np-head"><div class="np-title">' + esc(__("Create New Lease")) + '</div><div class="np-sub">' + esc(__("Saudi Ejar-style • Residential (exempt) or Commercial (15% VAT + ZATCA)")) + "</div></div>" +
			'<div class="np-steps np-steps7">' +
			steps.map((l, i) => {
				const n = i + 1, cls = n === S.step ? "active" : n < S.step ? "done" : "";
				return '<div class="np-step ' + cls + '"><div class="np-step-n">' + (n < S.step ? "✓" : n) + '</div><div class="np-step-l">' + esc(l) + "</div></div>";
			}).reverse().join("") +
			"</div>" +
			'<div class="np-card">' + [step1, step2, step3, step4, step5, step6, step7][S.step - 1]() + "</div></div>"
		);
		bind();
	}

	function step1() {
		return (
			head(__("Step 1: Contract Type & Basics"), __("Choose the contract type (drives VAT) and the unit(s) and dates.")) +
			'<div class="np-cards" style="grid-template-columns:1fr 1fr;">' +
			card("residential", "home", __("Residential"), __("Individuals — apartments, villas • no VAT"), S.c.contract_type, "data-ctype", isCommercial() ? "" : "معفى") +
			card("commercial", "project", __("Commercial"), __("Companies — offices, shops • 15% VAT + ZATCA"), S.c.contract_type, "data-ctype", isCommercial() ? "ZATCA 15%" : "") +
			"</div>" +
			'<div class="np-grid2" style="margin-top:14px;">' +
			sel(__("Contract Subtype"), "contract_subtype", [["New", __("New")], ["Renewal", __("Renewal")], ["Transfer", __("Transfer")]]) +
			field(__("Ejar Contract No"), "ejar_contract_no", { ph: "10354210842 / 1-0" }) + "</div>" +
			unitPicker() +
			'<div class="np-grid2" style="margin-top:14px;">' +
			field(__("Tenant Name"), "tenant_name", { req: 1 }) + field(__("Tenant Phone"), "tenant_phone", { ph: "05XXXXXXXX" }) +
			field(__("Start Date"), "start_date", { req: 1, type: "date" }) + field(__("End Date"), "end_date", { req: 1, type: "date" }) +
			field(__("Hijri Start"), "hijri_start_date", { ph: "1447-07-12" }) + field(__("Hijri End"), "hijri_end_date", { ph: "1448-07-11" }) +
			field(__("Sealing Date"), "sealing_date", { type: "date" }) + "</div>" +
			nav(false)
		);
	}
	function step2() {
		const co = S.c.lessor_org_type !== "Individual";
		return head(__("Step 2: Lessor (Owner)"), __("The lessor may be an individual or a company.")) +
			'<div class="np-grid2">' + sel(__("Lessor Org Type"), "lessor_org_type", orgTypes()) + "</div>" +
			(co ? '<div class="np-label">' + esc(__("Company Details")) + '</div><div class="np-grid2">' +
				field(__("Lessor Company"), "lessor_company_name") + field(__("Lessor CR No"), "lessor_cr_number") +
				field(__("Lessor Unified No (700)"), "lessor_unified_number") + field(__("Lessor VAT No"), "lessor_vat_number") + "</div>" : "") +
			nav(false);
	}
	function step3() {
		const co = isCommercial() || S.c.tenant_org_type !== "Individual";
		return head(__("Step 3: Tenant"), __("Individual or company. Commercial contracts require a VAT number.")) +
			'<div class="np-grid2">' + sel(__("Tenant Org Type"), "tenant_org_type", orgTypes()) + "</div>" +
			(co ? '<div class="np-label">' + esc(__("Company Details")) + '</div><div class="np-grid2">' +
				field(__("Tenant Company"), "tenant_company_name") + field(__("Tenant CR No"), "tenant_cr_number") +
				field(__("Tenant Unified No (700)"), "tenant_unified_number") +
				field(__("Tenant VAT Number") + (isCommercial() ? " *" : ""), "tenant_vat_number", { ph: "3XXXXXXXXXXXXX3" }) + "</div>" : "") +
			'<div class="np-label">' + esc(__("Guarantor (optional)")) + '</div><div class="np-grid2">' +
			field(__("Guarantor Name"), "guarantor_name") + field(__("Guarantor ID"), "guarantor_id_number") + field(__("Guarantor Phone"), "guarantor_phone") + "</div>" +
			nav(false);
	}
	function step4() {
		return head(__("Step 4: Broker (optional)"), __("If a broker mediated the contract, add their details.")) +
			'<div class="np-grid2">' + field(__("Broker Company"), "broker_company_name") + field(__("Broker CR No"), "broker_cr_number") +
			field(__("Broker Employee"), "broker_employee_name") + "</div>" + nav(false);
	}
	function step5() {
		return head(__("Step 5: Deed & Business Activity"), __("Title deed info; for commercial contracts, activity details.")) +
			'<div class="np-label">' + esc(__("Title Deed (الصك)")) + '</div><div class="np-grid2">' +
			field(__("Deed No"), "deed_number") + sel(__("Deed Type"), "deed_type", [["", "—"], ["Paper", __("Paper")], ["Electronic", __("Electronic")], ["Other", __("Other")]]) +
			field(__("Deed Issuer"), "deed_issuer", { ph: "كتابة العدل" }) + field(__("Deed Issue Date"), "deed_issue_date", { type: "date" }) + "</div>" +
			(isCommercial() ? '<div class="np-label">' + esc(__("Business Activity (النشاط)")) + '</div><div class="np-grid2">' +
				field(__("Business Name"), "business_name") + field(__("Business CR No"), "business_cr_number") +
				field(__("ISIC Activity"), "isic_activity") + field(__("License No"), "license_number") + "</div>" : "") +
			nav(false);
	}
	function step6() {
		const t = totals();
		return head(__("Step 6: Financial Terms"), __("Billing cycle, fees and utilities. VAT is derived from the contract type.")) +
			'<div class="np-grid2">' +
			sel(__("Billing Cycle"), "billing_cycle", [["Monthly", __("Monthly")], ["Quarterly", __("Quarterly")], ["Semi-Annual", __("Semi-Annual")], ["Annual", __("Annual")]]) +
			field(__("Payment Day"), "payment_day", { type: "number", ph: "1-28" }) +
			field(__("Retainer (عربون)"), "retainer_fee", { type: "number" }) +
			field(__("Extra Security Deposit"), "security_deposit_extra", { type: "number" }) + "</div>" +
			'<div class="np-summary"><div class="np-summary-h">' + esc(__("Rent Summary")) + " — " + esc(isCommercial() ? __("15% — Commercial") : __("Exempt — Residential (0%)")) + "</div>" +
			'<div class="np-sumgrid">' +
			sumCell(__("Annual Rent"), money(t.annual)) + sumCell(__("Installments"), t.inst) + sumCell(__("Per Installment"), money(t.per)) +
			(isCommercial() ? sumCell(__("VAT"), money(t.vat)) + sumCell(__("Total incl. VAT"), money(t.total)) : "") + "</div></div>" +
			'<div class="np-label">' + esc(__("One-time Fees (outside contract value)")) + '</div><div class="np-grid2">' +
			field(__("Brokerage Fee"), "brokerage_fee", { type: "number" }) + field(__("General Services"), "general_services_amount", { type: "number" }) +
			field(__("Waste Removal"), "waste_removal_fee", { type: "number" }) + field(__("Engineering Supervision"), "engineering_supervision_fee", { type: "number" }) +
			field(__("Unit Finishing"), "unit_finishing_fee", { type: "number" }) + "</div>" +
			'<div class="np-label">' + esc(__("Annual Utilities")) + '</div><div class="np-grid2">' +
			field(__("Electricity (annual)"), "electricity_annual", { type: "number" }) + field(__("Water (annual)"), "water_annual", { type: "number" }) +
			field(__("Gas (annual)"), "gas_annual", { type: "number" }) + field(__("Parking (annual)"), "parking_annual", { type: "number" }) +
			field(__("Parking Lots Rented"), "parking_lots_rented", { type: "number" }) + field(__("Payment Methods"), "payment_methods_text") + "</div>" +
			'<div class="np-label">' + esc(__("Obligations (optional)")) + "</div>" +
			area(__("Lessor Obligations"), "lessor_obligations") + area(__("Tenant Obligations"), "tenant_obligations") + area(__("Additional Terms"), "additional_terms") +
			nav(false);
	}
	function step7() {
		const t = totals();
		const row = (k, v) => '<tr><td>' + esc(k) + '</td><td>' + esc(v) + "</td></tr>";
		return head(__("Step 7: Review & Publish"), __("Review before saving; you can go back to any step.")) +
			'<table class="np-review"><tbody>' +
			row(__("Contract Type"), isCommercial() ? __("Commercial") : __("Residential")) +
			row(__("Tenant"), S.c.tenant_name) + row(__("Units"), S.units.length) +
			row(__("Start"), S.c.start_date) + row(__("End"), S.c.end_date) +
			row(__("Billing Cycle"), __(S.c.billing_cycle)) +
			row(__("Annual Rent"), money(t.annual)) + row(__("Per Installment"), money(t.per)) +
			(isCommercial() ? row(__("VAT"), money(t.vat)) + row(__("Total incl. VAT"), money(t.total)) : "") +
			"</tbody></table>" +
			'<label class="np-check" style="margin-top:16px;"><input type="checkbox" id="nl-publish" ' + (S.publish ? "checked" : "") + "> " + esc(__("Activate contract now (generates the rent schedule)")) + "</label>" +
			nav(true);
	}

	// ---- unit picker -------------------------------------------------------
	function unitPicker() {
		const props = [];
		const seen = {};
		S.available.forEach((u) => { if (!seen[u.property]) { seen[u.property] = 1; props.push(u); } });
		const usedNames = S.units.map((u) => u.unit);
		const unitOpts = S.available.filter((u) => u.property === S.picker.property && usedNames.indexOf(u.unit) < 0);
		const chips = S.units.map((u, i) =>
			'<div class="np-chip2">' + esc(u.unit_number) + " · " + money(u.annual_rent) + '/' + esc(__("yr")) +
			' <span class="np-rmunit" data-i="' + i + '">✕</span></div>').join("");
		return (
			'<div class="np-label">' + esc(__("Units")) + ' <span class="np-req">*</span> <span class="np-count">(' + S.units.length + ")</span></div>" +
			'<div class="np-picker">' +
			'<select class="np-pk" data-pk="property"><option value="">' + esc(__("Select property")) + "</option>" +
			props.map((p) => '<option value="' + esc(p.property) + '"' + (p.property === S.picker.property ? " selected" : "") + ">" + esc(p.property_name || p.property) + "</option>").join("") + "</select>" +
			'<select class="np-pk" data-pk="unit"><option value="">' + esc(__("Select unit")) + "</option>" +
			unitOpts.map((u) => '<option value="' + esc(u.unit) + '"' + (u.unit === S.picker.unit ? " selected" : "") + ">" + esc(u.unit_number) + "</option>").join("") + "</select>" +
			'<input type="number" class="np-pk" data-pk="annual_rent" placeholder="' + esc(__("Annual rent")) + '" value="' + esc(S.picker.annual_rent) + '">' +
			'<input type="number" class="np-pk" data-pk="deposit" placeholder="' + esc(__("Deposit")) + '" value="' + esc(S.picker.deposit) + '">' +
			'<button class="btn btn-primary np-addunit">+ ' + esc(__("Add")) + "</button></div>" +
			(chips ? '<div class="np-chips2">' + chips + "</div>" : "")
		);
	}

	// ---- small builders ----------------------------------------------------
	function head(t, s) { return '<div class="np-step-head"><h3>' + esc(t) + "</h3><p>" + esc(s) + "</p></div>"; }
	function orgTypes() { return [["Individual", __("Individual")], ["Commercial", __("Company")], ["Government", __("Government")], ["Non-profit", __("Non-profit")]]; }
	function card(key, icon, title, sub, selected, attr, badge) {
		return '<div class="np-cardsel ' + (key === selected ? "sel" : "") + '" ' + attr + '="' + key + '"><div class="np-cardsel-ic">' + frappe.utils.icon(icon, "lg") + '</div><div class="np-cardsel-t">' + esc(title) + '</div><div class="np-cardsel-s">' + esc(sub) + "</div>" + (badge ? '<div class="np-badge">' + esc(badge) + "</div>" : "") + "</div>";
	}
	function sumCell(l, v) { return '<div><span>' + esc(v) + "</span>" + esc(l) + "</div>"; }
	function nav(last) {
		return '<div class="np-nav">' + (S.step > 1 ? '<button class="btn btn-default np-back">' + esc(__("Previous")) + "</button>" : "<span></span>") +
			(last ? '<button class="btn btn-primary np-submit">' + esc(__("Create Contract")) + "</button>" : '<button class="btn btn-primary np-next">' + esc(__("Next")) + "</button>") + "</div>";
	}

	// ---- binding -----------------------------------------------------------
	function bind() {
		$root.find("[data-k]").on("input change", function () { S.c[$(this).data("k")] = $(this).val(); });
		$root.find("[data-ctype]").on("click", function () { S.c.contract_type = $(this).data("ctype"); render(); });
		$root.find(".np-pk").on("input change", function () { S.picker[$(this).data("pk")] = $(this).val(); if ($(this).data("pk") === "property") { S.picker.unit = ""; render(); } });
		$root.find(".np-addunit").on("click", addUnit);
		$root.find(".np-rmunit").on("click", function () { S.units.splice($(this).data("i"), 1); render(); });
		$root.find("#nl-publish").on("change", function () { S.publish = this.checked; });
		$root.find(".np-next").on("click", next);
		$root.find(".np-back").on("click", () => { S.step--; render(); });
		$root.find(".np-submit").on("click", submit);
	}
	function addUnit() {
		const pk = S.picker;
		if (!pk.unit) { frappe.msgprint(__("Select a unit.")); return; }
		const src = S.available.find((u) => u.unit === pk.unit) || {};
		S.units.push({
			unit: pk.unit, unit_number: src.unit_number, property: src.property, property_name: src.property_name,
			annual_rent: parseFloat(pk.annual_rent) || parseFloat(src.market_rent) || 0,
			deposit: parseFloat(pk.deposit) || parseFloat(src.deposit_amount) || 0,
		});
		S.picker = { property: pk.property, unit: "", annual_rent: "", deposit: "" };
		render();
	}
	function next() {
		if (S.step === 1) {
			if (!S.units.length) { frappe.msgprint(__("Add at least one unit.")); return; }
			if (!S.c.tenant_name.trim()) { frappe.msgprint(__("Tenant name is required.")); return; }
			if (!S.c.start_date || !S.c.end_date) { frappe.msgprint(__("Start and end dates are required.")); return; }
		}
		if (S.step === 3 && isCommercial() && !(S.c.tenant_vat_number || "").trim()) {
			frappe.msgprint(__("A commercial contract requires the tenant VAT number (15 digits, starts & ends with 3).")); return;
		}
		S.step++; render();
	}
	function submit() {
		const contract = Object.assign({}, S.c, { contract_type: isCommercial() ? "Commercial" : "Residential" });
		frappe.call({
			method: "bunood_realestate.real_estate.doctype.lease_contract.lease_contract.create_lease_from_wizard",
			args: { data: JSON.stringify({ contract, units: S.units, publish: S.publish ? 1 : 0 }) },
			freeze: true, freeze_message: __("Creating contract..."),
			callback: (r) => {
				if (r.message && r.message.lease) {
					frappe.show_alert({ message: __("Contract {0} created", [r.message.lease]), indicator: "green" });
					frappe.set_route("Form", "Lease Contract", r.message.lease);
				}
			},
		});
	}

	function injectStyles() {
		// The property wizard's base styles double as the lease wizard's; define the
		// base here too (guarded by id) in case that page hasn't loaded this session.
		if (!document.getElementById("np-styles")) injectBase();
		injectLeaseExtra();
	}
	function injectBase() {
		const css = `
.np-wrap{background:var(--bnd-bg,#F6F8F7);}
.np{max-width:1080px;margin:0 auto;padding:8px 4px 48px;}
.np-head{background:linear-gradient(120deg,var(--bnd-primary,#1F5145),var(--bnd-primary-700,#12352C));color:#fff;border-radius:16px;padding:22px 26px;}
.np-title{font-size:24px;font-weight:800;}.np-sub{opacity:.85;margin-top:4px;}
.np-steps{display:flex;gap:10px;margin:16px 0;flex-wrap:wrap;}
.np-steps7 .np-step{min-width:96px;}
.np-step{flex:1;background:#fff;border:1px solid var(--bnd-border,#DCE6E2);border-radius:14px;padding:12px;text-align:center;}
.np-step.active{border-color:var(--bnd-primary,#1F5145);box-shadow:0 0 0 2px var(--bnd-primary-050,#E8F0ED);}
.np-step-n{width:28px;height:28px;border-radius:50%;background:var(--bnd-surface-2,#EEF3F1);color:var(--bnd-muted,#5C6B66);display:inline-grid;place-items:center;font-weight:700;}
.np-step.active .np-step-n,.np-step.done .np-step-n{background:var(--bnd-primary,#1F5145);color:#fff;}
.np-step-l{margin-top:5px;font-size:12px;color:var(--bnd-ink,#12251F);font-weight:600;}
.np-card{background:#fff;border:1px solid var(--bnd-border,#DCE6E2);border-radius:16px;padding:24px;}
.np-step-head h3{font-weight:800;}.np-step-head p{color:var(--bnd-muted,#5C6B66);margin-bottom:14px;}
.np-label{font-weight:700;margin:14px 0 8px;color:var(--bnd-ink,#12251F);}
.np-grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
@media(max-width:700px){.np-grid2{grid-template-columns:1fr;}}
.np-f{display:flex;flex-direction:column;gap:4px;}
.np-f label{font-size:13px;color:var(--bnd-muted,#5C6B66);}
.np-f input,.np-f select,.np-f textarea{border:1px solid var(--bnd-border,#DCE6E2);border-radius:8px;padding:9px 12px;background:#fff;}
.np-req{color:#DC2626;}
.np-cards{display:grid;gap:12px;}
.np-cardsel{position:relative;border:1px solid var(--bnd-border,#DCE6E2);border-radius:14px;padding:16px;text-align:center;cursor:pointer;transition:.12s;}
.np-cardsel:hover{border-color:var(--bnd-primary,#1F5145);}
.np-cardsel.sel{border-color:var(--bnd-primary,#1F5145);background:var(--bnd-primary-050,#E8F0ED);box-shadow:0 0 0 2px var(--bnd-primary-050,#E8F0ED);}
.np-cardsel-ic{color:var(--bnd-primary,#1F5145);}
.np-cardsel-t{font-weight:700;margin-top:6px;}
.np-cardsel-s{font-size:12px;color:var(--bnd-muted,#5C6B66);margin-top:2px;}
.np-nav{display:flex;justify-content:space-between;margin-top:22px;}
.np-empty{opacity:.6;padding:20px;text-align:center;}
.np-check{font-weight:600;}
`;
		$("<style id='np-styles'>").text(css).appendTo(document.head);
	}
	function injectLeaseExtra() {
		if (document.getElementById("nl-styles")) return;
		const css = `
.np-badge{position:absolute;top:10px;inset-inline-start:10px;background:var(--bnd-gold-050,#FBF3E4);color:var(--bnd-gold-600,#A9781F);border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700;}
.np-picker{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0;}
.np-pk{border:1px solid var(--bnd-border,#DCE6E2);border-radius:8px;padding:8px 10px;}
.np-pk[data-pk=annual_rent],.np-pk[data-pk=deposit]{width:130px;}
.np-chips2{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;}
.np-chip2{background:var(--bnd-primary-050,#E8F0ED);color:var(--bnd-primary,#1F5145);border-radius:999px;padding:5px 12px;font-weight:600;font-size:13px;}
.np-rmunit{cursor:pointer;margin-inline-start:6px;color:#DC2626;}
.np-count{color:var(--bnd-muted,#5C6B66);font-weight:400;}
.np-summary{background:var(--bnd-gold-050,#FBF3E4);border:1px solid #EBD9B4;border-radius:14px;padding:16px;margin:14px 0;}
.np-summary-h{font-weight:700;color:var(--bnd-gold-600,#A9781F);margin-bottom:10px;}
.np-sumgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;text-align:center;}
.np-sumgrid span{display:block;font-size:20px;font-weight:800;color:var(--bnd-ink,#12251F);}
.np-review{width:100%;border-collapse:collapse;}
.np-review td{border:1px solid var(--bnd-border,#DCE6E2);padding:8px 12px;}
.np-review td:first-child{color:var(--bnd-muted,#5C6B66);width:40%;}
`;
		$("<style id='nl-styles'>").text(css).appendTo(document.head);
	}
};
