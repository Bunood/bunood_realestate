// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Occupancy Summary"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "property", label: __("Property"), fieldtype: "Link", options: "Property" },
	],
};
