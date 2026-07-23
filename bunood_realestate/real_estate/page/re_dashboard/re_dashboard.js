// Copyright (c) 2026, Bunood and contributors
// Real Estate dashboard — KPI cards + monthly rent chart + top overdue.
// Reuses the Bunood theme's .bnd-* card styling (installed alongside).

frappe.pages["real-estate-dashboard"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: __("Real Estate Dashboard"), single_column: true });

	const $c = $(wrapper).find(".layout-main-section").addClass("bnd-dash-wrap");
	$c.html('<div class="bnd-dash">' + skeleton() + "</div>");
	const $dash = $c.find(".bnd-dash");

	frappe
		.call({ method: "bunood_realestate.real_estate.re_dashboard.dashboard_data" })
		.then((r) => render($dash, r.message || {}))
		.catch(() => $dash.html('<div class="bnd-empty">' + __("Could not load the dashboard") + "</div>"));

	function skeleton() {
		return (
			'<div class="bnd-skel-hero"></div><div class="bnd-kpis">' +
			new Array(5).fill('<div class="bnd-skel-card"></div>').join("") +
			"</div>"
		);
	}

	function esc(s) {
		return frappe.utils.escape_html(String(s == null ? "" : s));
	}

	function render($dash, data) {
		const kpis = data.kpis || [];
		const hero =
			'<div class="bnd-hero"><div class="bnd-hero-greet">' +
			esc(__("Real Estate Overview")) +
			'</div><div class="bnd-hero-sub">' +
			esc(__("Occupancy, leases and collections at a glance")) +
			'</div><span class="bnd-hero-badge">' +
			esc(frappe.datetime.str_to_user(frappe.datetime.get_today())) +
			"</span></div>";

		const cards = kpis
			.map(
				(k) =>
					'<div class="bnd-card tone-' +
					esc(k.tone || "green") +
					'"><div class="bnd-kpi"><div class="bnd-kpi-ic">' +
					frappe.utils.icon(k.icon || "small-file", "md") +
					'</div><div><div class="bnd-kpi-val">' +
					esc(k.value) +
					'</div><div class="bnd-kpi-lbl">' +
					esc(k.label) +
					"</div></div></div></div>"
			)
			.join("");

		const grid =
			'<div class="bnd-grid">' +
			'<div class="bnd-card bnd-chart-card"><div class="bnd-card-title">' +
			esc(__("Rent Scheduled (last 12 months)")) +
			'</div><div class="re-chart"></div></div>' +
			'<div class="bnd-card bnd-list-card"><div class="bnd-card-title">' +
			esc(__("Top Overdue")) +
			"</div>" +
			overdueList(data.overdue || []) +
			"</div></div>";

		$dash.html(hero + '<div class="bnd-kpis">' + cards + "</div>" + grid);
		drawChart($dash.find(".re-chart")[0], data.chart || { labels: [], values: [] });
	}

	function overdueList(rows) {
		if (!rows.length) return '<div class="bnd-empty">' + __("No overdue balances") + "</div>";
		return (
			'<div class="bnd-list">' +
			rows
				.map(
					(r) =>
						'<div class="bnd-row"><div><div class="bnd-row-t">' +
						esc(r.customer) +
						'</div><div class="bnd-row-s">' +
						esc(r.property || "") +
						'</div></div><div class="bnd-row-amt">' +
						esc(r.amount_fmt) +
						"</div></div>"
				)
				.join("") +
			"</div>"
		);
	}

	function drawChart(el, chart) {
		if (!el || !chart.labels || !chart.labels.length) return;
		const color = (window.bunood && bunood.theme && bunood.theme.primary && bunood.theme.primary()) || "#1F5145";
		new frappe.Chart(el, {
			data: {
				labels: chart.labels,
				datasets: [{ name: __("Rent Scheduled"), values: chart.values }],
			},
			type: "bar",
			height: 260,
			colors: [color],
			axisOptions: { xIsSeries: true },
		});
	}
};
