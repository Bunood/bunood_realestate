// Copyright (c) 2026, Bunood and contributors
// New Property wizard — a guided 3-step creation flow (Basics → Owner/Operation/
// Location → Unit builder) that creates the Property AND all its units atomically.
// Mirrors the bunood_core property wizard; styled with the Bunood green+gold theme.

frappe.pages["new-property"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("New Property"), single_column: true });
	injectStyles();

	const $root = $(wrapper).find(".layout-main-section").addClass("np-wrap");
	const S = {
		step: 1,
		basics: {
			property_name: "", code: "", deed_number: "", construction_year: "",
			property_type: "residential", residential_subtype: "Families",
			total_area_sqm: "", floors_count: 1,
		},
		operation: "owned",
		fee: 5,
		owner: { owner_name: "", owner_phone: "", owner_id_num: "", owner_email: "", owner_iban: "", owner_nationality: "السعودية", owner_date_of_birth: "", owner_address: "" },
		location: { city: "الرياض", district: "", street: "", building_no: "", postal_code: "", description: "" },
		floors: [],
		sameAll: true,
	};
	applyPreset("residential");
	render();

	// ---- presets -----------------------------------------------------------
	function block(type, count, rooms, living, bath, area, rent, deposit) {
		return { type, count, rooms, living, bath, area, rent, deposit };
	}
	function applyPreset(name) {
		if (name === "villa") S.floors = [{ blocks: [block("Villa", 1, 5, 2, 4, 400, 8000, 8000)] }];
		else if (name === "commercial")
			S.floors = Array.from({ length: 5 }, () => ({ blocks: [block("Office", 6, 0, 0, 1, 60, 2500, 2500)] }));
		else if (name === "mixed")
			S.floors = [
				{ blocks: [block("Shop", 4, 0, 0, 1, 45, 3000, 3000), block("Office", 2, 0, 0, 1, 60, 2500, 2500)] },
				{ blocks: [block("Apartment", 4, 2, 1, 2, 120, 1800, 1800)] },
				{ blocks: [block("Apartment", 4, 2, 1, 2, 120, 1800, 1800)] },
			];
		else S.floors = Array.from({ length: 4 }, () => ({ blocks: [block("Apartment", 4, 2, 1, 2, 120, 1800, 1800)] }));
		S.sameAll = name === "residential" || name === "commercial";
	}

	// ---- unit collection + totals -----------------------------------------
	function effectiveFloors() {
		if (S.sameAll && S.floors.length) return S.floors.map(() => ({ blocks: S.floors[0].blocks }));
		return S.floors;
	}
	function collectUnits() {
		const out = [];
		effectiveFloors().forEach((fl, i) => {
			const floor = i + 1;
			let seq = 1;
			(fl.blocks || []).forEach((b) => {
				for (let c = 0; c < (parseInt(b.count) || 0); c++) {
					// Per-unit override values when the block is customised, else block defaults.
					const o = (b.overrides && b.overrides[c]) || b;
					out.push({
						floor, unit_number: "F" + floor + "-" + String(seq).padStart(2, "0"),
						unit_type: b.type, rooms_count: o.rooms, living_rooms_count: o.living,
						bathrooms: o.bath, area_sqm: o.area, monthly_rent: o.rent, deposit_amount: o.deposit,
					});
					seq++;
				}
			});
		});
		return out;
	}

	function ensureOverrides(b) {
		const n = parseInt(b.count) || 0;
		if (!b.overrides) b.overrides = [];
		while (b.overrides.length < n)
			b.overrides.push({ rooms: b.rooms, living: b.living, bath: b.bath, area: b.area, rent: b.rent, deposit: b.deposit });
		b.overrides.length = n;
	}
	function totals() {
		const units = collectUnits();
		const rent = units.reduce((s, u) => s + (parseFloat(u.monthly_rent) || 0), 0);
		return { units: units.length, floors: effectiveFloors().length, rent };
	}

	// ---- render ------------------------------------------------------------
	function render() {
		$root.html(
			'<div class="np">' + header() + stepper() +
			'<div class="np-card">' + (S.step === 1 ? step1() : S.step === 2 ? step2() : step3()) + "</div>" +
			"</div>"
		);
		bind();
	}

	function header() {
		return (
			'<div class="np-head"><div class="np-title">' + esc(__("Create New Property")) +
			'</div><div class="np-sub">' + esc(__("3 easy steps • Basics → Owner & Operation → Units")) + "</div></div>"
		);
	}
	function stepper() {
		const labels = [__("Basics"), __("Owner & Operation & Location"), __("Generate Units")];
		return (
			'<div class="np-steps">' +
			labels
				.map((l, i) => {
					const n = i + 1, cls = n === S.step ? "active" : n < S.step ? "done" : "";
					return '<div class="np-step ' + cls + '"><div class="np-step-n">' + (n < S.step ? "✓" : n) + '</div><div class="np-step-l">' + esc(l) + "</div></div>";
				})
				.reverse()
				.join("") +
			"</div>"
		);
	}

	function field(label, key, group, opts) {
		opts = opts || {};
		const v = S[group][key] == null ? "" : S[group][key];
		const req = opts.req ? ' <span class="np-req">*</span>' : "";
		const t = opts.type || "text";
		const ph = opts.ph ? ' placeholder="' + esc(opts.ph) + '"' : "";
		return (
			'<div class="np-f"><label>' + esc(label) + req + "</label>" +
			'<input type="' + t + '" data-g="' + group + '" data-k="' + key + '" value="' + esc(v) + '"' + ph + "></div>"
		);
	}
	function selectField(label, key, group, options) {
		const v = S[group][key];
		return (
			'<div class="np-f"><label>' + esc(label) + "</label><select data-g=\"" + group + '" data-k="' + key + '">' +
			options.map((o) => '<option value="' + esc(o[0]) + '"' + (o[0] === v ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") +
			"</select></div>"
		);
	}
	function cardRow(items, selected, dataAttr) {
		return (
			'<div class="np-cards">' +
			items
				.map(
					(it) =>
						'<div class="np-cardsel ' + (it.key === selected ? "sel" : "") + '" ' + dataAttr + '="' + esc(it.key) + '">' +
						'<div class="np-cardsel-ic">' + frappe.utils.icon(it.icon, "lg") + "</div>" +
						'<div class="np-cardsel-t">' + esc(it.title) + '</div><div class="np-cardsel-s">' + esc(it.sub) + "</div></div>"
				)
				.join("") +
			"</div>"
		);
	}

	function step1() {
		const showSub = S.basics.property_type !== "commercial";
		return (
			'<div class="np-step-head"><h3>' + esc(__("Step 1: Basics")) + "</h3><p>" + esc(__("Property identity: name, code, deed, type.")) + "</p></div>" +
			'<div class="np-grid2">' + field(__("Property Name"), "property_name", "basics", { req: 1, ph: __("e.g. Al-Suwaidi Building") }) +
			field(__("Code (internal reference)"), "code", "basics", { ph: "PROP-001" }) +
			field(__("Deed Number"), "deed_number", "basics", { ph: "123456789" }) +
			field(__("Construction Year"), "construction_year", "basics", { type: "number", ph: "2020" }) + "</div>" +
			'<div class="np-label">' + esc(__("Property Type")) + "</div>" +
			cardRow(
				[
					{ key: "residential", icon: "home", title: __("Residential"), sub: __("Buildings, apartments, villas") },
					{ key: "commercial", icon: "project", title: __("Commercial"), sub: __("Offices, shops, warehouses") },
					{ key: "mixed", icon: "retail", title: __("Mixed"), sub: __("Shops below + apartments above") },
				],
				S.basics.property_type,
				"data-type"
			) +
			'<div class="np-grid2" style="margin-top:16px;">' +
			(showSub ? selectField(__("Residential Subtype"), "residential_subtype", "basics", [["Families", __("Families")], ["Singles", __("Singles")], ["Group", __("Group")]]) : "") +
			field(__("Total Area (sqm)"), "total_area_sqm", "basics", { type: "number" }) +
			field(__("Floors Count"), "floors_count", "basics", { type: "number" }) + "</div>" +
			navRow(false)
		);
	}

	function step2() {
		return (
			'<div class="np-step-head"><h3>' + esc(__("Step 2: Owner + Operation + Location")) + "</h3><p>" + esc(__("Choose the owner, operation type and location.")) + "</p></div>" +
			'<div class="np-label">' + esc(__("Operation Type")) + "</div>" +
			cardRow(
				[
					{ key: "owned", icon: "home", title: __("Investment (Owned)"), sub: __("Owned outright — all income is yours") },
					{ key: "managed", icon: "users", title: __("Property Management"), sub: __("Owned by another — you take a %") },
					{ key: "master_lease", icon: "sync", title: __("Master Lease"), sub: __("Rent the whole building then sublet") },
				],
				S.operation,
				"data-op"
			) +
			(S.operation === "managed" ? '<div class="np-grid2" style="margin-top:12px;"><div class="np-f"><label>' + esc(__("Your share of income %")) + '</label><input type="number" data-g="root" data-k="fee" value="' + esc(S.fee) + '"></div></div>' : "") +
			'<div class="np-label" style="margin-top:16px;">' + esc(__("Owner Details")) + "</div>" +
			'<div class="np-grid2">' + field(__("Owner Name"), "owner_name", "owner", { req: 1 }) +
			field(__("Owner Phone"), "owner_phone", "owner", { ph: "05XXXXXXXX" }) +
			field(__("Owner ID Number"), "owner_id_num", "owner") +
			field(__("Owner Email"), "owner_email", "owner", { type: "email" }) +
			field(__("Owner IBAN"), "owner_iban", "owner", { ph: "SA00 0000 0000 0000 0000 0000" }) +
			field(__("Owner Date of Birth"), "owner_date_of_birth", "owner", { type: "date" }) +
			selectField(__("Nationality"), "owner_nationality", "owner", [["السعودية", "السعودية"], ["مصر", "مصر"], ["اليمن", "اليمن"], ["الأردن", "الأردن"], ["الهند", "الهند"], ["باكستان", "باكستان"], ["أخرى", __("Other")]]) +
			field(__("Owner Address"), "owner_address", "owner") + "</div>" +
			'<div class="np-label" style="margin-top:16px;">' + esc(__("Location")) + "</div>" +
			'<div class="np-grid2">' + field(__("City"), "city", "location") + field(__("District"), "district", "location") +
			field(__("Street"), "street", "location") + field(__("Building No"), "building_no", "location") +
			field(__("Postal Code"), "postal_code", "location") + "</div>" +
			'<div class="np-f"><label>' + esc(__("Property Notes")) + '</label><textarea data-g="location" data-k="description" rows="2">' + esc(S.location.description) + "</textarea></div>" +
			navRow(false)
		);
	}

	function step3() {
		const t = totals();
		return (
			'<div class="np-step-head"><h3>' + esc(__("Step 3: Building Composition")) + "</h3><p>" + esc(__("Pick a preset or configure floors — the live preview updates as you go.")) + "</p></div>" +
			'<div class="np-label">' + esc(__("Quick Preset")) + "</div>" +
			'<div class="np-cards np-presets">' +
			[
				{ key: "villa", icon: "home", title: __("Villa"), sub: __("1 unit • 5 rooms") },
				{ key: "residential", icon: "home", title: __("Residential Building"), sub: __("4 floors × 4 apartments") },
				{ key: "mixed", icon: "retail", title: __("Mixed"), sub: __("Shops + offices + apartments") },
				{ key: "commercial", icon: "project", title: __("Office Tower"), sub: __("5 floors × 6 offices") },
			]
				.map((p) => '<div class="np-cardsel" data-preset="' + p.key + '"><div class="np-cardsel-ic">' + frappe.utils.icon(p.icon, "lg") + '</div><div class="np-cardsel-t">' + esc(p.title) + '</div><div class="np-cardsel-s">' + esc(p.sub) + "</div></div>")
				.join("") +
			"</div>" +
			'<div class="np-builder">' +
			'<div class="np-preview"><div class="np-preview-h">' + esc(__("Building Preview")) + "</div>" + previewHtml() + "</div>" +
			'<div class="np-floors"><label class="np-check"><input type="checkbox" id="np-same" ' + (S.sameAll ? "checked" : "") + "> " + esc(__("All floors identical")) + "</label>" +
			floorsHtml() +
			'<button class="btn btn-default np-addfloor">+ ' + esc(__("Add Floor")) + "</button></div>" +
			"</div>" +
			'<div class="np-totals"><div><span>' + t.units + "</span>" + esc(__("Total Units")) + "</div><div><span>" + t.floors + "</span>" + esc(__("Floors")) + "</div><div><span>" + frappe.format(t.rent, { fieldtype: "Currency" }) + "</span>" + esc(__("Expected Monthly Rent")) + "</div></div>" +
			navRow(true)
		);
	}

	function floorsHtml() {
		const flrs = S.sameAll ? S.floors.slice(0, 1) : S.floors;
		return flrs
			.map((fl, fi) => {
				const blocks = (fl.blocks || [])
					.map((b, bi) => blockHtml(b, fi, bi))
					.join("");
				return (
					'<div class="np-floor" data-fi="' + fi + '"><div class="np-floor-h">' + esc(__("Floor")) + " " + (fi + 1) +
					(S.floors.length > 1 && !S.sameAll ? ' <span class="np-rmfloor" data-fi="' + fi + '">✕</span>' : "") + "</div>" +
					blocks +
					'<button class="btn btn-xs np-addblock" data-fi="' + fi + '">+ ' + esc(__("Another type on this floor")) + "</button></div>"
				);
			})
			.join("") + (S.sameAll && S.floors.length ? '<div class="np-mirror">' + esc(__("(mirrored to all {0} floors)").replace("{0}", S.floors.length)) + "</div>" : "");
	}
	function blockHtml(b, fi, bi) {
		const num = (label, key, val) => '<div class="np-bf"><label>' + esc(label) + '</label><input type="number" data-fi="' + fi + '" data-bi="' + bi + '" data-bk="' + key + '" value="' + esc(val) + '"></div>';
		const types = [["Apartment", __("Apartment")], ["Shop", __("Shop")], ["Office", __("Office")], ["Villa", __("Villa")], ["Warehouse", __("Warehouse")], ["Other", __("Other")]];
		const count = parseInt(b.count) || 0;
		return (
			'<div class="np-blockwrap"><div class="np-block"><div class="np-bf"><label>' + esc(__("Type")) + '</label><select data-fi="' + fi + '" data-bi="' + bi + '" data-bk="type">' +
			types.map((o) => '<option value="' + o[0] + '"' + (o[0] === b.type ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") + "</select></div>" +
			num(__("Count"), "count", b.count) + num(__("Rooms"), "rooms", b.rooms) + num(__("Living"), "living", b.living) +
			num(__("Baths"), "bath", b.bath) + num(__("Area"), "area", b.area) + num(__("Rent/mo"), "rent", b.rent) + num(__("Deposit"), "deposit", b.deposit) +
			((S.floors[fi] && S.floors[fi].blocks.length > 1) ? ' <span class="np-rmblock" data-fi="' + fi + '" data-bi="' + bi + '">✕</span>' : "") +
			"</div>" +
			(count
				? '<div class="np-ov"><span class="np-ov-toggle" data-fi="' + fi + '" data-bi="' + bi + '">' + (b.expand ? "▾ " : "▸ ") + esc(__("Customize each unit")) + " (" + count + ")</span>" +
				  (b.expand && b.overrides ? ' <span class="np-ov-reset" data-fi="' + fi + '" data-bi="' + bi + '">' + esc(__("Reset from default")) + "</span>" : "") +
				  (b.expand ? overrideTable(b, fi, bi) : "") + "</div>"
				: "") +
			"</div>"
		);
	}
	function overrideTable(b, fi, bi) {
		ensureOverrides(b);
		const head = ["#", __("Rooms"), __("Living"), __("Baths"), __("Area"), __("Rent/mo"), __("Deposit")]
			.map((h) => "<th>" + esc(h) + "</th>").join("");
		const rows = b.overrides
			.map((o, oi) => {
				const cell = (k) => '<td><input type="number" data-fi="' + fi + '" data-bi="' + bi + '" data-oi="' + oi + '" data-ok="' + k + '" value="' + esc(o[k]) + '"></td>';
				return "<tr><td>F" + (fi + 1) + "-" + String(oi + 1).padStart(2, "0") + "</td>" +
					cell("rooms") + cell("living") + cell("bath") + cell("area") + cell("rent") + cell("deposit") + "</tr>";
			})
			.join("");
		return '<div class="np-ovtable"><table><thead><tr>' + head + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
	}
	function previewHtml() {
		const flrs = effectiveFloors();
		if (!flrs.length) return '<div class="np-empty">—</div>';
		return flrs
			.map((fl, i) => {
				const floor = flrs.length - i; // top floor first
				const src = fl.blocks;
				const chips = src.map((b) => '<span class="np-chip">' + (parseInt(b.count) || 0) + "×</span>").join("");
				const count = src.reduce((s, b) => s + (parseInt(b.count) || 0), 0);
				return '<div class="np-prow"><span class="np-fbadge">F' + floor + "</span><span class=\"np-pcount\">" + count + " " + esc(__("units")) + "</span><span class=\"np-pchips\">" + chips + "</span></div>";
			})
			.join("");
	}

	function navRow(last) {
		return (
			'<div class="np-nav">' +
			(S.step > 1 ? '<button class="btn btn-default np-back">' + esc(__("Previous")) + "</button>" : "<span></span>") +
			(last
				? '<button class="btn btn-primary np-submit">' + esc(__("Create Property + Units")) + "</button>"
				: '<button class="btn btn-primary np-next">' + esc(__("Next")) + "</button>") +
			"</div>"
		);
	}

	// ---- binding -----------------------------------------------------------
	function bind() {
		$root.find("input,select,textarea").on("input change", function () {
			const g = $(this).data("g"), k = $(this).data("k");
			if (g && k) {
				if (g === "root") S[k] = $(this).val();
				else S[g][k] = $(this).val();
			}
			const fi = $(this).data("fi"), bi = $(this).data("bi"), bk = $(this).data("bk");
			const oi = $(this).data("oi"), ok = $(this).data("ok");
			if (fi != null && bi != null && oi != null && ok) {
				const b = S.floors[fi].blocks[bi];
				ensureOverrides(b);
				b.overrides[oi][ok] = $(this).val();
				refreshStep3();
			} else if (fi != null && bi != null && bk) {
				S.floors[fi].blocks[bi][bk] = $(this).val();
				if (bk === "count" && S.floors[fi].blocks[bi].expand) render();
				else refreshStep3();
			}
		});
		$root.find("[data-type]").on("click", function () { S.basics.property_type = $(this).data("type"); render(); });
		$root.find("[data-op]").on("click", function () { S.operation = $(this).data("op"); render(); });
		$root.find("[data-preset]").on("click", function () { applyPreset($(this).data("preset")); render(); });
		$root.find(".np-ov-toggle").on("click", function () {
			const b = S.floors[$(this).data("fi")].blocks[$(this).data("bi")];
			b.expand = !b.expand;
			if (b.expand) ensureOverrides(b);
			render();
		});
		$root.find(".np-ov-reset").on("click", function () {
			S.floors[$(this).data("fi")].blocks[$(this).data("bi")].overrides = null;
			render();
		});
		$root.find("#np-same").on("change", function () { S.sameAll = this.checked; render(); });
		$root.find(".np-addfloor").on("click", function () { S.floors.push({ blocks: [block("Apartment", 4, 2, 1, 2, 120, 1800, 1800)] }); render(); });
		$root.find(".np-rmfloor").on("click", function () { S.floors.splice($(this).data("fi"), 1); render(); });
		$root.find(".np-addblock").on("click", function () { S.floors[$(this).data("fi")].blocks.push(block("Apartment", 2, 2, 1, 1, 100, 1500, 1500)); render(); });
		$root.find(".np-rmblock").on("click", function () { S.floors[$(this).data("fi")].blocks.splice($(this).data("bi"), 1); render(); });
		$root.find(".np-next").on("click", next);
		$root.find(".np-back").on("click", () => { S.step--; render(); });
		$root.find(".np-submit").on("click", submit);
	}
	function refreshStep3() {
		const t = totals();
		$root.find(".np-preview").html('<div class="np-preview-h">' + esc(__("Building Preview")) + "</div>" + previewHtml());
		$root.find(".np-totals").html('<div><span>' + t.units + "</span>" + esc(__("Total Units")) + "</div><div><span>" + t.floors + "</span>" + esc(__("Floors")) + "</div><div><span>" + frappe.format(t.rent, { fieldtype: "Currency" }) + "</span>" + esc(__("Expected Monthly Rent")) + "</div>");
	}
	function next() {
		if (S.step === 1 && !S.basics.property_name.trim()) { frappe.msgprint(__("Property name is required.")); return; }
		if (S.step === 2 && !S.owner.owner_name.trim()) { frappe.msgprint(__("Owner name is required.")); return; }
		S.step++; render();
	}
	function submit() {
		const payload = {
			property: Object.assign({}, S.basics, S.owner, S.location, { operation_type: S.operation, management_fee_percentage: S.operation === "managed" ? S.fee : 0 }),
			units: collectUnits(),
		};
		frappe.call({
			method: "bunood_realestate.real_estate.doctype.property.property.create_property_with_units",
			args: { data: JSON.stringify(payload) },
			freeze: true,
			freeze_message: __("Creating property and units..."),
			callback: (r) => {
				if (r.message && r.message.property) {
					frappe.show_alert({ message: __("Created {0} with {1} unit(s)", [r.message.property, r.message.units]), indicator: "green" });
					frappe.set_route("Form", "Property", r.message.property);
				}
			},
		});
	}

	function esc(s) { return frappe.utils.escape_html(String(s == null ? "" : s)); }

	function injectStyles() {
		if (document.getElementById("np-styles")) return;
		const css = `
.np-wrap{background:var(--bnd-bg,#F6F8F7);}
.np{max-width:1080px;margin:0 auto;padding:8px 4px 48px;}
.np-head{background:linear-gradient(120deg,var(--bnd-primary,#1F5145),var(--bnd-primary-700,#12352C));color:#fff;border-radius:16px;padding:22px 26px;}
.np-title{font-size:24px;font-weight:800;}
.np-sub{opacity:.85;margin-top:4px;}
.np-steps{display:flex;gap:12px;margin:16px 0;}
.np-step{flex:1;background:#fff;border:1px solid var(--bnd-border,#DCE6E2);border-radius:14px;padding:14px;text-align:center;}
.np-step.active{border-color:var(--bnd-primary,#1F5145);box-shadow:0 0 0 2px var(--bnd-primary-050,#E8F0ED);}
.np-step-n{width:30px;height:30px;border-radius:50%;background:var(--bnd-surface-2,#EEF3F1);color:var(--bnd-muted,#5C6B66);display:inline-grid;place-items:center;font-weight:700;}
.np-step.active .np-step-n{background:var(--bnd-primary,#1F5145);color:#fff;}
.np-step.done .np-step-n{background:var(--bnd-primary,#1F5145);color:#fff;}
.np-step-l{margin-top:6px;font-size:13px;color:var(--bnd-ink,#12251F);font-weight:600;}
.np-card{background:#fff;border:1px solid var(--bnd-border,#DCE6E2);border-radius:16px;padding:24px;}
.np-step-head h3{font-weight:800;}
.np-step-head p{color:var(--bnd-muted,#5C6B66);margin-bottom:14px;}
.np-label{font-weight:700;margin:14px 0 8px;color:var(--bnd-ink,#12251F);}
.np-grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
@media(max-width:700px){.np-grid2{grid-template-columns:1fr;}}
.np-f{display:flex;flex-direction:column;gap:4px;}
.np-f label{font-size:13px;color:var(--bnd-muted,#5C6B66);}
.np-f input,.np-f select,.np-f textarea{border:1px solid var(--bnd-border,#DCE6E2);border-radius:8px;padding:9px 12px;background:#fff;}
.np-req{color:#DC2626;}
.np-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
.np-presets{grid-template-columns:repeat(4,1fr);}
@media(max-width:700px){.np-cards,.np-presets{grid-template-columns:1fr 1fr;}}
.np-cardsel{border:1px solid var(--bnd-border,#DCE6E2);border-radius:14px;padding:16px;text-align:center;cursor:pointer;transition:.12s;}
.np-cardsel:hover{border-color:var(--bnd-primary,#1F5145);}
.np-cardsel.sel{border-color:var(--bnd-primary,#1F5145);background:var(--bnd-primary-050,#E8F0ED);box-shadow:0 0 0 2px var(--bnd-primary-050,#E8F0ED);}
.np-cardsel-ic{color:var(--bnd-primary,#1F5145);}
.np-cardsel-t{font-weight:700;margin-top:6px;}
.np-cardsel-s{font-size:12px;color:var(--bnd-muted,#5C6B66);margin-top:2px;}
.np-builder{display:grid;grid-template-columns:1fr 1.4fr;gap:16px;margin-top:16px;}
@media(max-width:800px){.np-builder{grid-template-columns:1fr;}}
.np-preview{background:var(--bnd-ink,#12251F);color:#fff;border-radius:14px;padding:16px;min-height:220px;}
.np-preview-h{font-weight:700;margin-bottom:10px;opacity:.9;}
.np-prow{display:flex;align-items:center;gap:8px;padding:8px 6px;border-top:1px solid rgba(255,255,255,.1);}
.np-fbadge{background:var(--bnd-gold,#C8923C);color:#fff;border-radius:6px;padding:2px 8px;font-weight:700;font-size:12px;}
.np-pcount{font-size:13px;opacity:.85;}
.np-chip{background:rgba(255,255,255,.14);border-radius:6px;padding:1px 6px;font-size:11px;margin-inline-start:3px;}
.np-floors{display:flex;flex-direction:column;gap:12px;}
.np-check{font-weight:600;}
.np-floor{border:1px dashed var(--bnd-border,#DCE6E2);border-radius:12px;padding:12px;}
.np-floor-h{font-weight:700;margin-bottom:8px;}
.np-rmfloor,.np-rmblock{color:#DC2626;cursor:pointer;float:inline-end;}
.np-block{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin-bottom:8px;}
.np-bf{display:flex;flex-direction:column;gap:2px;}
.np-bf label{font-size:11px;color:var(--bnd-muted,#5C6B66);}
.np-bf input,.np-bf select{width:72px;border:1px solid var(--bnd-border,#DCE6E2);border-radius:7px;padding:6px 8px;}
.np-bf select{width:100px;}
.np-addblock{font-size:12px;}
.np-ov{margin:4px 0 8px;}
.np-ov-toggle{cursor:pointer;color:var(--bnd-primary,#1F5145);font-weight:600;font-size:12px;}
.np-ov-reset{cursor:pointer;color:var(--bnd-muted,#5C6B66);font-size:11px;margin-inline-start:10px;text-decoration:underline;}
.np-ovtable{overflow-x:auto;margin-top:6px;}
.np-ovtable table{width:100%;border-collapse:collapse;font-size:12px;}
.np-ovtable th,.np-ovtable td{border:1px solid var(--bnd-border,#DCE6E2);padding:3px 5px;text-align:center;}
.np-ovtable th{background:var(--bnd-surface-2,#EEF3F1);color:var(--bnd-muted,#5C6B66);}
.np-ovtable input{width:64px;border:1px solid var(--bnd-border,#DCE6E2);border-radius:6px;padding:4px 6px;}
.np-mirror{font-size:12px;color:var(--bnd-muted,#5C6B66);}
.np-totals{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;background:var(--bnd-ink,#12251F);color:#fff;border-radius:14px;padding:16px;margin-top:16px;text-align:center;}
.np-totals span{display:block;font-size:22px;font-weight:800;color:var(--bnd-gold,#E0B15E);}
.np-nav{display:flex;justify-content:space-between;margin-top:22px;}
.np-empty{opacity:.6;padding:20px;text-align:center;}
`;
		$("<style id='np-styles'>").text(css).appendTo(document.head);
	}
};
