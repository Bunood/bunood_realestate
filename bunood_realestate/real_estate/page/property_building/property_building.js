// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
/* Property Building view — renders each property as a stacked building (floors + unit
 * cells colour-coded by live status) plus a units board with full info. Data:
 * previews.property_building (occupancy from Active leases). Self-contained styling. */

frappe.pages["property-building"].on_page_load = function (wrapper) {
	bnd_inject_building_css();
	const page = frappe.ui.make_app_page({
		parent: wrapper, title: __("Property Building"), single_column: true,
	});
	const $body = $('<div class="bnd-bld"></div>').appendTo(page.body);
	const propField = page.add_field({ fieldname: "property", label: __("Property"), fieldtype: "Link", options: "Property" });

	const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));
	const STATE = {
		Occupied: { c: "#2D6F5E", t: "#fff", label: __("Occupied") },
		Reserved: { c: "var(--bnd-gold, #C8923C)", t: "#1a1205", label: __("Reserved") },
		Vacant: { c: "#E7EAE6", t: "#5B6760", label: __("Vacant") },
		Maintenance: { c: "#B5563C", t: "#fff", label: __("Maintenance") },
	};
	let CURRENT = null;

	function money(v, currency) {
		return frappe.format(flt(v), { fieldtype: "Currency" }, { currency: currency });
	}

	function unitCell(u) {
		const st = STATE[u.state] || STATE.Vacant;
		return `<button class="bld-cell" data-unit="${esc(u.name)}" title="${esc(u.unit_number)} — ${esc(st.label)}"
			style="background:${st.c};color:${st.t};">
			<span class="bld-cell__no">${esc(u.unit_number)}</span>
			<span class="bld-cell__ty">${esc(u.unit_type || "")}</span></button>`;
	}

	function floorRow(f) {
		const label = f.floor === 0 || f.floor === null ? __("Ground") : __("Floor {0}", [f.floor]);
		return `<div class="bld-floor">
			<div class="bld-floor__tag">${esc(label)}<span class="bld-floor__n">${f.units.length}</span></div>
			<div class="bld-floor__cells">${f.units.map(unitCell).join("")}</div></div>`;
	}

	function legend(t, currency) {
		const item = (state, n) =>
			`<span class="bld-leg"><span class="bld-leg__dot" style="background:${STATE[state].c}"></span>${STATE[state].label}<b>${n}</b></span>`;
		return `<div class="bld-legend">
			${item("Occupied", t.occupied)} ${item("Reserved", t.reserved)}
			${item("Vacant", t.vacant)} ${item("Maintenance", t.maintenance)}
			<span class="bld-leg bld-leg--occ">${__("Occupancy")}<b>${t.occupancy_pct}%</b></span></div>`;
	}

	function boardCard(u, currency) {
		const st = STATE[u.state] || STATE.Vacant;
		const meta = [
			u.unit_type,
			u.area_sqm ? `${u.area_sqm} ${__("m²")}` : null,
			u.rooms_count ? `${u.rooms_count} ${__("rooms")}` : null,
			u.bathrooms ? `${u.bathrooms} ${__("baths")}` : null,
		].filter(Boolean).map(esc).join(" · ");
		return `<div class="bld-card" data-unit="${esc(u.name)}" role="button" tabindex="0" aria-label="${esc(u.unit_number)} — ${st.label}">
			<div class="bld-card__top">
				<span class="bld-card__no">${esc(u.unit_number)}</span>
				<span class="bld-badge" style="background:${st.c};color:${st.t};">${st.label}</span>
			</div>
			<div class="bld-card__meta">${meta || "—"}</div>
			<div class="bld-card__foot">
				<span>${u.tenant_name ? esc(u.tenant_name) : `<span class="bld-muted">${__("No tenant")}</span>`}</span>
				<b>${money(u.rent, currency)}</b>
			</div></div>`;
	}

	function render(d) {
		CURRENT = d;
		if (!d) { $body.html(""); return; }
		if (!d.floors.length) {
			$body.html(`<div class="bld-empty">${__("This property has no units yet.")}</div>`);
			return;
		}
		const c = d.currency;
		const building = d.floors.map(floorRow).join("");
		const board = d.floors.flatMap((f) => f.units).map((u) => boardCard(u, c)).join("");
		$body.html(`
			<div class="bld-head">
				<div><div class="bld-head__name">${esc(d.property_name)}</div>
				<div class="bld-head__sub">${d.totals.total} ${__("Units")} · ${d.totals.occupancy_pct}% ${__("Occupancy")}</div></div>
			</div>
			${legend(d.totals, c)}
			<div class="bld-building"><div class="bld-roof"></div>${building}<div class="bld-base"></div></div>
			<h4 class="bld-board-h">${__("Units")}</h4>
			<div class="bld-board">${board}</div>`);
	}

	function unitDialog(name) {
		if (!CURRENT) return;
		const u = CURRENT.floors.flatMap((f) => f.units).find((x) => x.name === name);
		if (!u) return;
		const st = STATE[u.state] || STATE.Vacant;
		const c = CURRENT.currency;
		const row = (k, v) => v ? `<tr><td style="padding:4px 8px;color:#6b7280;">${esc(k)}</td><td style="padding:4px 8px;font-weight:600;">${v}</td></tr>` : "";
		const html = `<div dir="auto">
			<div style="margin-bottom:10px;"><span class="bld-badge" style="background:${st.c};color:${st.t};">${st.label}</span></div>
			<table style="width:100%;border-collapse:collapse;">
				${row(__("Unit"), esc(u.unit_number))}
				${row(__("Type"), esc(u.unit_type))}
				${row(__("Floor"), u.floor === 0 || u.floor === null ? __("Ground") : u.floor)}
				${row(__("Area"), u.area_sqm ? esc(u.area_sqm) + " " + __("m²") : "")}
				${row(__("Rooms"), u.rooms_count)}
				${row(__("Bathrooms"), u.bathrooms)}
				${row(__("View"), esc(u.view_type))}
				${row(__("Tenant"), u.tenant_name ? esc(u.tenant_name) : "")}
				${row(__("Rent"), money(u.rent, c))}
			</table></div>`;
		const dlg = new frappe.ui.Dialog({
			title: __("Unit {0}", [u.unit_number]),
			fields: [{ fieldtype: "HTML", fieldname: "b", options: html }],
			primary_action_label: __("Open"),
			primary_action() { dlg.hide(); frappe.set_route("Form", "Real Estate Unit", u.name); },
		});
		dlg.show();
	}

	function load() {
		const property = propField.get_value();
		if (!property) {
			$body.html(`<div class="bld-empty">${__("Select a property to see its building.")}</div>`);
			return;
		}
		frappe.call({
			method: "bunood_realestate.real_estate.previews.property_building",
			args: { property: property },
			freeze: true, freeze_message: __("Loading..."),
			callback: (r) => render(r.message),
			error: () => {
				// Never leave the page silently blank on a server error.
				$body.html(`<div class="bld-empty">${__("Could not load the building view. Check the property and try again.")}</div>`);
			},
		});
	}

	propField.$input.on("change", load);
	$body.on("click", ".bld-cell, .bld-card", function () { unitDialog($(this).data("unit")); });
	// Keyboard parity for the role="button" cards (unit cells are real <button>s already).
	$body.on("keydown", ".bld-card", function (e) {
		if (e.key === "Enter" || e.key === " ") { e.preventDefault(); unitDialog($(this).data("unit")); }
	});
	load();

	frappe.pages["property-building"].bnd_set_property = function (name) {
		propField.set_value(name);
		load();
	};
};

function bnd_inject_building_css() {
	if (document.getElementById("bnd-bld-css")) return;
	const css = `
.bnd-bld{max-width:1100px;margin-inline:auto;padding:12px 6px 40px;}
.bnd-bld .bld-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;}
.bnd-bld .bld-head__name{font-size:20px;font-weight:800;color:var(--bnd-primary,#1F5145);}
.bnd-bld .bld-head__sub{color:#6b7280;font-size:13px;}
.bnd-bld .bld-legend{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:6px 0 14px;font-size:12.5px;color:#374151;}
.bnd-bld .bld-leg{display:inline-flex;align-items:center;gap:6px;}
.bnd-bld .bld-leg b{margin-inline-start:4px;color:var(--bnd-primary,#1F5145);}
.bnd-bld .bld-leg__dot{width:12px;height:12px;border-radius:3px;display:inline-block;}
.bnd-bld .bld-leg--occ{margin-inline-start:auto;font-weight:600;}
.bnd-bld .bld-building{border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;background:linear-gradient(180deg,#f7f9f8,#eef2f1);box-shadow:0 8px 24px rgba(15,42,36,.08);}
.bnd-bld .bld-roof{height:14px;background:repeating-linear-gradient(90deg,var(--bnd-gold,#C8923C) 0 10px,var(--bnd-primary-deep,#0F2A24) 10px 14px,var(--bnd-mint,#9BE0CB) 14px 22px,var(--bnd-primary-deep,#0F2A24) 22px 26px);}
.bnd-bld .bld-base{height:10px;background:var(--bnd-primary-deep,#0F2A24);}
.bnd-bld .bld-floor{display:flex;align-items:stretch;border-top:1px solid rgba(15,42,36,.06);}
.bnd-bld .bld-floor:first-of-type{border-top:none;}
.bnd-bld .bld-floor__tag{flex:0 0 92px;display:flex;flex-direction:column;justify-content:center;padding:10px 12px;background:rgba(15,42,36,.04);font-size:12px;font-weight:700;color:var(--bnd-primary,#1F5145);}
.bnd-bld .bld-floor__n{font-size:11px;color:#6b7280;font-weight:600;}
.bnd-bld .bld-floor__cells{flex:1;display:flex;flex-wrap:wrap;gap:8px;padding:10px 12px;}
.bnd-bld .bld-cell{width:72px;height:56px;border:none;border-radius:9px;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;box-shadow:0 1px 2px rgba(0,0,0,.12);transition:transform .12s cubic-bezier(.34,1.56,.64,1);}
.bnd-bld .bld-cell:hover{transform:translateY(-3px) scale(1.03);}
.bnd-bld .bld-cell__no{font-weight:800;font-size:13px;}
.bnd-bld .bld-cell__ty{font-size:9.5px;opacity:.85;max-width:66px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.bnd-bld .bld-board-h{margin:22px 0 10px;}
.bnd-bld .bld-board{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px;}
.bnd-bld .bld-card:focus-visible{outline:2px solid var(--bnd-gold,#C8923C);outline-offset:2px;}
.bnd-bld .bld-card{border:1px solid #e8eae7;border-radius:12px;background:#fff;padding:12px 14px;cursor:pointer;transition:box-shadow .14s,transform .14s;}
.bnd-bld .bld-card:hover{box-shadow:0 10px 24px rgba(15,42,36,.1);transform:translateY(-2px);}
.bnd-bld .bld-card__top{display:flex;justify-content:space-between;align-items:center;}
.bnd-bld .bld-card__no{font-weight:800;color:#0B1F1A;}
.bnd-bld .bld-badge{font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px;}
.bnd-bld .bld-card__meta{color:#6b7280;font-size:12px;margin:6px 0;}
.bnd-bld .bld-card__foot{display:flex;justify-content:space-between;align-items:center;font-size:13px;border-top:1px dashed #eef1ef;padding-top:8px;}
.bnd-bld .bld-muted{color:#9ca3af;}
.bnd-bld .bld-empty{padding:40px;text-align:center;color:#6b7280;}
@media(max-width:640px){.bnd-bld .bld-floor__tag{flex-basis:64px;}.bnd-bld .bld-cell{width:60px;height:50px;}}
`;
	const s = document.createElement("style");
	s.id = "bnd-bld-css";
	s.textContent = css;
	document.head.appendChild(s);
}
