// Copyright (c) 2026, Bunood and contributors
// Real Estate Command Center — the "Sadu Modern" cockpit (bunood_core design),
// rendered inside a Frappe Page. All figures are company-scoped and GL/lease-sourced
// (re_dashboard.dashboard_data). Styling: bunood_theme/public/css/bunood_cockpit.css.

frappe.pages["real-estate-dashboard"].on_page_load = function (wrapper) {
	bnd_inject_cockpit_css();
	frappe.ui.make_app_page({ parent: wrapper, title: __("Real Estate Command Center"), single_column: true });

	const $c = $(wrapper).find(".layout-main-section").addClass("bnd-dash-wrap");
	const dir = frappe.utils.is_rtl && frappe.utils.is_rtl() ? "rtl" : "ltr";
	$c.html('<div class="bnd-ck" dir="' + dir + '">' + skeleton() + "</div>");
	const $ck = $c.find(".bnd-ck");

	frappe
		.call({ method: "bunood_realestate.real_estate.re_dashboard.dashboard_data" })
		.then((r) => render($ck, r.message || {}))
		.catch(() => $ck.html('<div class="ck-empty">' + __("Could not load the dashboard") + "</div>"));

	const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));
	const ic = (n) => frappe.utils.icon(n, "sm");
	const money = (v) => frappe.format(v || 0, { fieldtype: "Currency" });

	function skeleton() {
		return (
			'<div class="ck-skel" style="height:120px"></div>' +
			'<div class="ck-hk" style="margin-top:14px">' +
			new Array(4).fill('<div class="ck-skel" style="height:96px"></div>').join("") +
			"</div>"
		);
	}

	function render($ck, data) {
		const ck = data.cockpit || {};
		const ring = ck.ring || { occupied: 0, reserved: 0, vacant: 0 };
		$ck.html(
			hero(ck) +
			hk(ck) +
			aging(data.overdue || [], ck) +
			sectionHead("map", __("Performance")) +
			'<div class="ck-grid">' +
				'<div class="col-4 card">' + '<div class="card__t">' + esc(__("Occupancy")) + "</div>" + ringCard(ring) + "</div>" +
				'<div class="col-8 card line-card">' + '<div class="card__t">' + esc(__("Rent Scheduled (12 months)")) + "</div>" + lineCard(data.chart || {}) + "</div>" +
			"</div>" +
			sectionHead("list", __("Follow-up & Activity")) +
			'<div class="ck-grid">' +
				'<div class="col-7 card"><div class="card__t">' + esc(__("Top Overdue")) + "</div>" + overdueQueue(data.overdue || []) + "</div>" +
				'<div class="col-5 card"><div class="card__t">' + esc(__("Snapshot")) + "</div>" + snapshot(ck) + "</div>" +
			"</div>"
		);
		requestAnimationFrame(() => $ck.find(".rank__bar i").each(function () { this.style.width = ($(this).data("w") || 0) + "%"; }));
	}

	function hero(ck) {
		const today = frappe.datetime.str_to_user(frappe.datetime.get_today());
		return (
			'<section class="ck-hero"><span class="ck-hero__sadu"></span><div class="ck-hero__in">' +
			'<div class="ck-hero__mark">' + ic("building") + "</div>" +
			'<div><div class="ck-hero__eyebrow">' + esc(__("Real Estate Command Center")) + "</div>" +
			'<div class="ck-hero__title">' + esc(__("Overview")) + "</div>" +
			'<div class="ck-hero__sub">' + esc(today) + "</div>" +
			'<div class="ck-hero__pills">' +
				pill("brass", (ck.occupancy_pct || 0) + "% " + __("Occupancy")) +
				pill("white", (ck.properties || 0) + " " + __("Properties")) +
				pill("white", (ck.units_total || 0) + " " + __("Units")) +
				pill("ink", (ck.collection_rate || 0) + "% " + __("Collection")) +
			"</div></div>" +
			'<div class="ck-hero__cta">' +
				'<button class="ck-btn ck-btn--brass" data-go="new-property">+ ' + esc(__("New Property")) + "</button>" +
				'<button class="ck-btn ck-btn--out" data-go="new-lease">+ ' + esc(__("New Lease")) + "</button>" +
			"</div></div></section>"
		);
	}
	function pill(kind, txt) { return '<span class="hpill hpill--' + kind + '"><span class="num">' + esc(txt) + "</span></span>"; }

	function hk(ck) {
		const card = (icon, label, val, brass, href) =>
			'<a class="ck-hk__c" href="' + (href || "#") + '"><div class="ck-hk__lbl">' + ic(icon) + esc(label) +
			'</div><div class="ck-hk__val num' + (brass ? " is-brass" : "") + '">' + esc(val) + "</div></a>";
		return (
			'<div class="ck-hk">' +
			card("pie-chart", __("Occupancy"), (ck.occupancy_pct || 0) + "%", true, "#Form/Real Estate Settings") +
			card("check", __("Collection Rate"), (ck.collection_rate || 0) + "%", false) +
			card("money", __("Collected (month)"), money(ck.collected_month), true) +
			card("alert", __("Outstanding"), money(ck.outstanding), false, "/app/rent-collections") +
			"</div>"
		);
	}

	function aging(overdue, ck) {
		if (!flt(ck.outstanding)) return "";
		return "";
	}

	function sectionHead(icon, title) {
		return '<div class="ck-h"><span class="ck-h__ic">' + ic(icon) + '</span><span class="ck-h__t">' + esc(title) + "</span></div>";
	}

	function ringCard(r) {
		const total = (r.occupied || 0) + (r.reserved || 0) + (r.vacant || 0);
		const R = 62, C = 2 * Math.PI * R;
		const segs = [
			{ n: r.occupied, col: "#2D6F5E", label: __("Occupied") },
			{ n: r.reserved, col: "#C8923C", label: __("Reserved") },
			{ n: r.vacant, col: "#D9DCD3", label: __("Vacant") },
		];
		let offset = 0;
		const arcs = segs.map((s) => {
			const frac = total ? s.n / total : 0;
			const len = frac * C;
			const el = '<circle cx="84" cy="84" r="' + R + '" fill="none" stroke="' + s.col + '" stroke-width="18" ' +
				'stroke-dasharray="' + len + " " + (C - len) + '" stroke-dashoffset="' + (-offset) + '" transform="rotate(-90 84 84)"></circle>';
			offset += len;
			return el;
		}).join("");
		const pct = total ? Math.round((r.occupied / total) * 100) : 0;
		const legend = segs.map((s) =>
			'<div class="leg__row"><span class="leg__dot" style="background:' + s.col + '"></span>' + esc(s.label) +
			'<span class="leg__n num">' + (s.n || 0) + "</span></div>").join("");
		return (
			'<div class="ring-wrap"><svg class="ring" viewBox="0 0 168 168">' + arcs +
			'<text class="ring__center" x="84" y="92" text-anchor="middle">' + pct + '%</text></svg>' +
			'<div class="leg">' + legend + "</div></div>"
		);
	}

	function lineCard(chart) {
		const vals = (chart && chart.values) || [];
		const labels = (chart && chart.labels) || [];
		if (!vals.length) return '<div class="ck-empty">' + __("No data") + "</div>";
		const W = 720, H = 200, pad = 16;
		const max = Math.max.apply(null, vals.concat([1]));
		const step = vals.length > 1 ? (W - pad * 2) / (vals.length - 1) : 0;
		const y = (v) => H - pad - (v / max) * (H - pad * 2);
		const pts = vals.map((v, i) => [pad + i * step, y(v)]);
		const path = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
		const area = "M" + pad + " " + (H - pad) + " " + pts.map((p) => "L" + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ") + " L" + (pad + (vals.length - 1) * step) + " " + (H - pad) + " Z";
		const last = pts[pts.length - 1];
		const dots = pts.map((p) => '<circle class="line__dot" cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3"></circle>').join("");
		const xl = labels.map((l) => "<span>" + esc(l) + "</span>").join("");
		return (
			'<svg class="line" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' +
			'<defs><linearGradient id="ckla" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2D6F5E" stop-opacity=".18"/><stop offset="1" stop-color="#2D6F5E" stop-opacity="0"/></linearGradient></defs>' +
			'<path d="' + area + '" fill="url(#ckla)"></path>' +
			'<path class="line__path" d="' + path + '"></path>' + dots +
			'<circle class="line__marker" cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="5"></circle>' +
			"</svg>" +
			'<div class="line__xlabels">' + xl + "</div>"
		);
	}

	function overdueQueue(rows) {
		if (!rows.length) return '<div class="ck-empty">' + __("No overdue balances") + "</div>";
		return '<div class="ck-queue">' + rows.map((r) =>
			'<a class="q q--danger" href="/app/sales-invoice?customer=' + encodeURIComponent(r.customer) + '"><span class="q__ic">' + ic("alert") + "</span>" +
			'<div><div class="q__t">' + esc(r.customer) + '</div><div class="q__s">' + esc(r.property || "") + "</div></div>" +
			'<span class="q__amt">' + esc(r.amount_fmt) + "</span></a>").join("") + "</div>";
	}

	function snapshot(ck) {
		const row = (label, val) =>
			'<div class="rank__row"><div style="flex:1"><div class="q__t">' + esc(label) + "</div></div>" +
			'<span class="q__amt num">' + esc(val) + "</span></div>";
		return (
			row(__("Active Leases"), ck.active_leases || 0) +
			row(__("Total Units"), ck.units_total || 0) +
			row(__("Collected (month)"), money(ck.collected_month)) +
			row(__("Expected (month)"), money(ck.expected_month))
		);
	}

	$c.on("click", "[data-go]", function () { frappe.set_route($(this).data("go")); });
};

// The "Sadu Modern" cockpit stylesheet ships WITH the app (self-contained, palette
// hardcoded, font fallbacks) and is injected once — so the command center renders as
// designed regardless of which desk theme is installed.
function bnd_inject_cockpit_css() {
	if (document.getElementById("bnd-ck-css")) return;
	const css = `
.bnd-ck{--g:#1F5145;--g-deep:#0F2A24;--g-ink:#0A201B;--g-soft:#2D6F5E;--g-line:#1B463C;--brass:#C8923C;--brass-bright:#F6D08A;--ochre-deep:#8E641E;--mint:#9BE0CB;--mint-bright:#C4F0E0;--mint-soft:#E3F1EB;--mint-deep:#2F7D66;--paper:#F4F5F0;--card:#FFFFFF;--rule:#E8E9E2;--rule-d:#D9DCD3;--ink:#0B1F1A;--stone:#5B6760;--rust:#B5563C;--rust-soft:#F2D8CE;--slate:#3D5260;--on-g-1:#fff;--on-g-2:rgba(255,255,255,.85);--on-g-3:rgba(255,255,255,.72);--spring:cubic-bezier(.34,1.56,.64,1);--ease-out:cubic-bezier(.22,1,.36,1);--num:'Space Grotesk','Readex Pro',system-ui,sans-serif;--head:'El Messiri','Readex Pro',system-ui,sans-serif;max-width:1280px;margin-inline:auto;padding:6px 2px 48px}
html[data-theme="dark"] .bnd-ck{--paper:#0C1512;--card:#111F1A;--rule:#223229;--rule-d:#223229;--ink:#EAF2EE;--stone:#8FA39B}
.bnd-ck .num{font-family:var(--num);font-variant-numeric:tabular-nums;letter-spacing:0}
.bnd-ck .ck-hero{position:relative;overflow:hidden;border-radius:18px;padding:26px 30px;color:#fff;background:linear-gradient(120deg,var(--g),var(--g-deep));box-shadow:0 18px 36px rgba(15,42,36,.16)}
.bnd-ck .ck-hero::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.5;background:repeating-linear-gradient(45deg,rgba(255,255,255,.035) 0 1.5px,transparent 1.5px 14px),repeating-linear-gradient(-45deg,rgba(255,255,255,.035) 0 1.5px,transparent 1.5px 14px)}
.bnd-ck .ck-hero__sadu{position:absolute;inset-block:0;inset-inline-start:0;width:7px;background:repeating-linear-gradient(180deg,var(--brass) 0 8px,var(--g-deep) 8px 12px,var(--mint) 12px 20px,var(--g-deep) 20px 24px)}
.bnd-ck .ck-hero__in{position:relative;display:flex;align-items:flex-start;gap:18px;flex-wrap:wrap}
.bnd-ck .ck-hero__mark{width:52px;height:52px;border-radius:12px;background:var(--g-deep);display:grid;place-items:center;color:var(--brass-bright);flex:0 0 auto}
.bnd-ck .ck-hero__mark svg{width:26px;height:26px}
.bnd-ck .ck-hero__eyebrow{color:var(--brass-bright);font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase}
.bnd-ck .ck-hero__title{font-family:var(--head);font-size:26px;font-weight:700;margin-top:3px}
.bnd-ck .ck-hero__sub{color:var(--on-g-2);font-size:13px;margin-top:4px}
.bnd-ck .ck-hero__pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.bnd-ck .hpill{border-radius:8px;padding:5px 12px;font-size:12px;font-weight:600}
.bnd-ck .hpill--brass{background:rgba(246,208,138,.16);color:var(--brass-bright);border:1px solid rgba(246,208,138,.3)}
.bnd-ck .hpill--white{background:rgba(255,255,255,.12);color:#fff}
.bnd-ck .hpill--ink{background:var(--g-ink);color:var(--on-g-2)}
.bnd-ck .ck-hero__cta{margin-inline-start:auto;display:flex;gap:10px;align-items:center}
.bnd-ck .ck-btn{border-radius:10px;padding:10px 18px;font-weight:700;font-size:13px;border:1px solid transparent;cursor:pointer;transition:transform .12s var(--spring)}
.bnd-ck .ck-btn:hover{transform:translateY(-2px)}
.bnd-ck .ck-btn--brass{background:var(--brass);color:#1a1205}
.bnd-ck .ck-btn--out{background:transparent;color:#fff;border-color:rgba(255,255,255,.4)}
.bnd-ck .ck-hk{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:14px}
@media(max-width:900px){.bnd-ck .ck-hk{grid-template-columns:1fr 1fr}}
.bnd-ck .ck-hk__c{position:relative;overflow:hidden;display:block;border-radius:14px;padding:16px 18px;background:var(--g-deep);color:#fff;text-decoration:none;transition:transform .16s var(--spring),box-shadow .16s var(--ease-out)}
.bnd-ck .ck-hk__c::before{content:"";position:absolute;inset-block-start:0;inset-inline:0;height:3px;background:var(--brass);transform:scaleX(0);transform-origin:inline-start;transition:transform .3s var(--ease-out)}
.bnd-ck .ck-hk__c:hover{transform:translateY(-5px) scale(1.018);box-shadow:0 18px 36px rgba(15,42,36,.3);color:#fff}
.bnd-ck .ck-hk__c:hover::before{transform:scaleX(1)}
.bnd-ck .ck-hk__lbl{display:flex;align-items:center;gap:7px;color:var(--on-g-2);font-size:12px;font-weight:600}
.bnd-ck .ck-hk__lbl svg{width:16px;height:16px;color:var(--brass-bright);transition:transform .16s var(--spring)}
.bnd-ck .ck-hk__c:hover .ck-hk__lbl svg{transform:scale(1.16) rotate(-7deg)}
.bnd-ck .ck-hk__val{font-family:var(--num);font-size:25px;font-weight:800;margin-top:8px}
.bnd-ck .ck-hk__val.is-brass{color:var(--brass-bright)}
.bnd-ck .ck-h{display:flex;align-items:center;gap:10px;margin:24px 0 12px}
.bnd-ck .ck-h__ic{width:28px;height:28px;border-radius:8px;background:var(--mint-soft);color:var(--g);display:grid;place-items:center}
.bnd-ck .ck-h__ic svg{width:16px;height:16px}
.bnd-ck .ck-h__t{font-family:var(--head);font-size:16px;font-weight:700;color:var(--ink)}
.bnd-ck .ck-grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}
.bnd-ck .col-4{grid-column:span 4}.bnd-ck .col-5{grid-column:span 5}.bnd-ck .col-7{grid-column:span 7}.bnd-ck .col-8{grid-column:span 8}
@media(max-width:900px){.bnd-ck .ck-grid>*{grid-column:span 12!important}}
.bnd-ck .card{background:var(--card);border:1px solid var(--rule);border-radius:16px;box-shadow:0 2px 8px rgba(11,31,26,.05)}
.bnd-ck .card__t{font-family:var(--head);font-weight:700;color:var(--ink);padding:16px 18px 4px}
.bnd-ck .ring-wrap{display:flex;align-items:center;gap:16px;padding:8px 18px 18px;flex-wrap:wrap}
.bnd-ck .ring{width:168px;height:168px;flex:0 0 auto}
.bnd-ck .ring__center{font-family:var(--num);font-size:34px;font-weight:800;fill:var(--ink)}
.bnd-ck .leg{display:flex;flex-direction:column;gap:8px}
.bnd-ck .leg__row{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--ink)}
.bnd-ck .leg__dot{width:10px;height:10px;border-radius:3px}
.bnd-ck .leg__n{font-family:var(--num);font-weight:700;margin-inline-start:auto}
.bnd-ck .line-card{padding-bottom:10px}
.bnd-ck .line{width:100%;height:220px;display:block}
.bnd-ck .line__path{fill:none;stroke:var(--g-soft);stroke-width:2.5}
.bnd-ck .line__dot{fill:var(--g-soft)}
.bnd-ck .line__marker{fill:var(--brass)}
.bnd-ck .line__xlabels{display:flex;justify-content:space-between;padding:4px 14px 0;color:var(--stone);font-size:11px}
.bnd-ck .ck-queue{display:flex;flex-direction:column;padding:6px 0 10px}
.bnd-ck .q{display:flex;align-items:center;gap:12px;padding:11px 18px;text-decoration:none;color:inherit;transition:background .12s}
.bnd-ck .q:hover{background:var(--paper)}
.bnd-ck .q__ic{width:36px;height:36px;border-radius:9px;background:var(--mint-soft);color:var(--g);display:grid;place-items:center;flex:0 0 auto}
.bnd-ck .q--danger .q__ic{background:var(--rust-soft);color:var(--rust)}
.bnd-ck .q__t{font-weight:600;color:var(--ink);font-size:13.5px}
.bnd-ck .q__s{font-size:12px;color:var(--stone)}
.bnd-ck .q__amt{margin-inline-start:auto;font-family:var(--num);font-weight:700;color:var(--ink);white-space:nowrap}
.bnd-ck .rank__row{display:flex;align-items:center;gap:12px;padding:10px 18px}
.bnd-ck .ck-empty{padding:26px;text-align:center;color:var(--stone)}
.bnd-ck .ck-skel{border-radius:16px;background:linear-gradient(90deg,var(--rule),var(--card),var(--rule));background-size:200% 100%;animation:ck-sh 1.3s infinite}
@keyframes ck-sh{0%{background-position:200% 0}100%{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.bnd-ck *{transition:none!important;animation:none!important}}
`;
	const s = document.createElement("style");
	s.id = "bnd-ck-css";
	s.textContent = css;
	document.head.appendChild(s);
}
