// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
/* Property Finance hub — per-property income / expense / net, sourced from the GL
 * via the Property accounting dimension. Self-contained styling so it renders
 * correctly regardless of the active desk theme. */

frappe.pages["property-finance"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Property Finance"),
		single_column: true,
	});
	const $body = $('<div class="bnd-pf" style="padding:10px 4px;"></div>').appendTo(page.body);

	const propField = page.add_field({ fieldname: "property", label: __("Property"), fieldtype: "Link", options: "Property" });
	const fromField = page.add_field({ fieldname: "from_date", label: __("From"), fieldtype: "Date" });
	const toField = page.add_field({ fieldname: "to_date", label: __("To"), fieldtype: "Date" });

	const esc = frappe.utils.escape_html;

	function money(v, currency) {
		return frappe.format(flt(v), { fieldtype: "Currency" }, { currency: currency });
	}

	function kpi(label, value, tone) {
		const colors = { green: "var(--bnd-primary, #1F5145)", gold: "var(--bnd-gold, #C8923C)", red: "var(--bnd-danger, #DC2626)", slate: "#475569" };
		const c = colors[tone] || colors.green;
		return `<div style="flex:1; min-width:150px; background:#fff; border:1px solid #e8eae7;
			border-radius:14px; padding:16px 18px; box-shadow:0 1px 2px rgba(0,0,0,.04);">
			<div style="font-size:12.5px; color:#6b7280;">${esc(label)}</div>
			<div style="font-size:22px; font-weight:800; color:${c}; margin-top:4px;">${value}</div>
		</div>`;
	}

	function breakdown(title, items, currency, tone) {
		if (!items || !items.length) {
			return `<div style="flex:1; min-width:260px;"><h4 style="margin:0 0 8px;">${esc(title)}</h4>
				<p class="text-muted" style="font-size:13px;">${__("Nothing recorded for this period.")}</p></div>`;
		}
		const bar = tone === "red" ? "var(--bnd-danger, #DC2626)" : "var(--bnd-primary, #1F5145)";
		const max = Math.max.apply(null, items.map((i) => Math.abs(flt(i.amount)))) || 1;
		let rows = "";
		items.forEach((i) => {
			const w = Math.round((Math.abs(flt(i.amount)) / max) * 100);
			rows += `<div style="margin:6px 0;">
				<div style="display:flex; justify-content:space-between; font-size:13px;">
					<span>${esc(i.account)}</span><strong>${money(i.amount, currency)}</strong></div>
				<div style="height:6px; background:#eef1ef; border-radius:4px; overflow:hidden; margin-top:3px;">
					<div style="height:100%; width:${w}%; background:${bar};"></div></div></div>`;
		});
		return `<div style="flex:1; min-width:260px;"><h4 style="margin:0 0 8px;">${esc(title)}</h4>${rows}</div>`;
	}

	function monthlyChart(m, currency) {
		if (!m || !m.labels || !m.labels.length) return "";
		const w = 720, h = 200, pad = 28, n = m.labels.length;
		const all = m.income.concat(m.expense).map((v) => flt(v));
		const max = Math.max.apply(null, all.concat([1]));
		const bw = (w - pad * 2) / n;
		let bars = "";
		for (let i = 0; i < n; i++) {
			const x0 = pad + i * bw;
			const ih = Math.round((flt(m.income[i]) / max) * (h - pad * 2));
			const eh = Math.round((flt(m.expense[i]) / max) * (h - pad * 2));
			bars += `<rect x="${x0 + bw * 0.15}" y="${h - pad - ih}" width="${bw * 0.3}" height="${ih}" fill="var(--bnd-primary, #1F5145)"></rect>`;
			bars += `<rect x="${x0 + bw * 0.55}" y="${h - pad - eh}" width="${bw * 0.3}" height="${eh}" fill="var(--bnd-gold, #C8923C)"></rect>`;
			if (i % 2 === 0) {
				bars += `<text x="${x0 + bw / 2}" y="${h - 8}" font-size="9" fill="#6b7280" text-anchor="middle">${esc(m.labels[i])}</text>`;
			}
		}
		return `<div style="margin-top:18px;"><h4 style="margin:0 0 6px;">${__("Income vs Expense (12 months)")}</h4>
			<div style="display:flex; gap:14px; font-size:12px; color:#6b7280; margin-bottom:6px;">
				<span><span style="display:inline-block;width:10px;height:10px;background:var(--bnd-primary,#1F5145);border-radius:2px;"></span> ${__("Income")}</span>
				<span><span style="display:inline-block;width:10px;height:10px;background:var(--bnd-gold,#C8923C);border-radius:2px;"></span> ${__("Expense")}</span></div>
			<div style="overflow-x:auto;"><svg viewBox="0 0 ${w} ${h}" style="width:100%; min-width:600px; height:auto;">
				<line x1="${pad}" y1="${h - pad}" x2="${w - pad}" y2="${h - pad}" stroke="#e5e7eb"></line>${bars}</svg></div></div>`;
	}

	function render($el, d) {
		if (!d) { $el.html(""); return; }
		const c = d.currency;
		const netTone = flt(d.net) >= 0 ? "green" : "red";
		const occ = d.occupancy || {};
		const html = `
			<div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px;">
				${kpi(__("Total Income"), money(d.total_income, c), "green")}
				${kpi(__("Total Expense"), money(d.total_expense, c), "gold")}
				${kpi(__("Net Profit"), money(d.net, c), netTone)}
				${kpi(__("Occupancy"), `${occ.pct || 0}% <span style="font-size:12px;color:#9ca3af;">(${occ.occupied || 0}/${occ.total || 0})</span>`, "slate")}
			</div>
			<div style="display:flex; gap:24px; flex-wrap:wrap; background:#fff; border:1px solid #e8eae7; border-radius:14px; padding:16px 18px;">
				${breakdown(__("Income"), d.income, c, "green")}
				${breakdown(__("Expense"), d.expense, c, "red")}
			</div>
			${monthlyChart(d.monthly, c)}`;
		$el.html(html);
	}

	function load() {
		const property = propField.get_value();
		if (!property) {
			$body.html(`<div class="text-muted" style="padding:28px;">${__("Select a property to see its finances.")}</div>`);
			return;
		}
		frappe.call({
			method: "bunood_realestate.real_estate.property_finance.property_finance",
			args: { property: property, from_date: fromField.get_value(), to_date: toField.get_value() },
			callback: (r) => render($body, r.message),
			error: () => {
				// Never leave the page silently blank on a server error.
				$body.html(`<div class="text-muted" style="padding:28px;">${__("Could not load the finance view. Check the filters and try again.")}</div>`);
			},
		});
	}

	propField.$input.on("change", load);
	fromField.$input.on("change", load);
	toField.$input.on("change", load);
	load();

	// Let other screens (e.g. the Property form's "Finance" button) open this page
	// already focused on a property.
	frappe.pages["property-finance"].bnd_set_property = function (name) {
		propField.set_value(name);
		load();
	};
};
