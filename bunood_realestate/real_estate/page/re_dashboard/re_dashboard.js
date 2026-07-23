// Copyright (c) 2026, Bunood and contributors
// Real Estate Command Center — the "Sadu Modern" cockpit (bunood_core design),
// rendered inside a Frappe Page. All figures are company-scoped and GL/lease-sourced
// (re_dashboard.dashboard_data). Styling: bunood_theme/public/css/bunood_cockpit.css.

frappe.pages["real-estate-dashboard"].on_page_load = function (wrapper) {
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
