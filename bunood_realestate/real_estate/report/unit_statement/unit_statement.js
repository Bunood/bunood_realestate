// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Unit Statement"] = {
	filters: [
		{
			fieldname: "unit",
			label: __("Unit"),
			fieldtype: "Link",
			options: "Real Estate Unit",
			reqd: 1,
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
