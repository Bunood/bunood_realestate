// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt
// Filing-period UX: the operator picks the client's ZATCA filing frequency
// (Monthly for large taxpayers, Quarterly standard, Annual for overviews) and the
// period — from/to dates fill automatically. "Custom" keeps free date entry.
// The server only ever sees from_date/to_date, so the query stays unchanged.

frappe.query_reports["RE VAT Summary"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "filing_period",
			label: __("Filing Period"),
			fieldtype: "Select",
			options: [
				{ value: "Quarterly", label: __("Quarterly") },
				{ value: "Monthly", label: __("Monthly") },
				{ value: "Annual", label: __("Annual") },
				{ value: "Custom", label: __("Custom") },
			],
			default: "Quarterly",
			on_change: () => {
				bnd_vat_refresh_period_options();
				bnd_vat_set_dates();
			},
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Select",
			options: bnd_vat_years(),
			default: String(new Date().getFullYear()),
			on_change: () => bnd_vat_set_dates(),
		},
		{
			fieldname: "period_no",
			label: __("Period"),
			fieldtype: "Select",
			options: [],
			on_change: () => bnd_vat_set_dates(),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.quarter_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],

	onload() {
		bnd_vat_refresh_period_options();
		bnd_vat_set_dates();
	},
};

function bnd_vat_years() {
	const y = new Date().getFullYear();
	const out = [];
	for (let i = 0; i < 6; i++) out.push(String(y - i));
	return out;
}

function bnd_vat_current_quarter() {
	return String(Math.floor(new Date().getMonth() / 3) + 1);
}

function bnd_vat_refresh_period_options() {
	const qr = frappe.query_report;
	if (!qr || !qr.get_filter) return;
	const fp = qr.get_filter_value("filing_period") || "Quarterly";
	const period = qr.get_filter("period_no");
	const year = qr.get_filter("year");
	if (!period) return;

	const MONTHS = [
		__("January"), __("February"), __("March"), __("April"), __("May"), __("June"),
		__("July"), __("August"), __("September"), __("October"), __("November"), __("December"),
	];
	let options = [];
	if (fp === "Quarterly") {
		options = [
			{ value: "1", label: __("Q1") + " (01–03)" },
			{ value: "2", label: __("Q2") + " (04–06)" },
			{ value: "3", label: __("Q3") + " (07–09)" },
			{ value: "4", label: __("Q4") + " (10–12)" },
		];
	} else if (fp === "Monthly") {
		options = MONTHS.map((m, i) => ({ value: String(i + 1), label: m }));
	}

	period.df.options = options;
	if (period.set_data) period.set_data(options);
	// Sensible default when switching frequency: current quarter / current month.
	if (fp === "Quarterly") period.set_input(bnd_vat_current_quarter());
	else if (fp === "Monthly") period.set_input(String(new Date().getMonth() + 1));

	// Year + Period only make sense for computed frequencies.
	if (period.toggle) period.toggle(fp === "Quarterly" || fp === "Monthly");
	if (year && year.toggle) year.toggle(fp !== "Custom");
}

function bnd_vat_set_dates() {
	const qr = frappe.query_report;
	if (!qr || !qr.get_filter) return;
	const fp = qr.get_filter_value("filing_period") || "Quarterly";
	if (fp === "Custom") return; // free date entry — never overwrite the user's dates

	const year = parseInt(qr.get_filter_value("year"), 10) || new Date().getFullYear();
	const pn = parseInt(qr.get_filter_value("period_no"), 10) || 1;

	let m0, m1; // first/last month (1-based)
	if (fp === "Annual") { m0 = 1; m1 = 12; }
	else if (fp === "Quarterly") { m0 = (pn - 1) * 3 + 1; m1 = m0 + 2; }
	else { m0 = pn; m1 = pn; }

	const pad = (n) => String(n).padStart(2, "0");
	const last_day = new Date(year, m1, 0).getDate(); // day 0 of next month = month end
	qr.set_filter_value({
		from_date: `${year}-${pad(m0)}-01`,
		to_date: `${year}-${pad(m1)}-${pad(last_day)}`,
	});
}
