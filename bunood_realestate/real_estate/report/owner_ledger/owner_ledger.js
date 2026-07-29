// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Owner Ledger"] = {
	filters: [
		{
			fieldname: "owner",
			label: __("Owner (Supplier)"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "property",
			label: __("Property"),
			fieldtype: "Link",
			options: "Property",
			get_query: function () {
				const owner = frappe.query_report.get_filter_value("owner");
				return owner ? { filters: { owner_party: owner } } : {};
			},
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.year_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
	],
};
