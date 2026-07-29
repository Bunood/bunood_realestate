// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Customer Statement"] = {
	filters: [
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer", reqd: 1 },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		// One report, many scenarios: optional real-estate scope (property / unit / lease).
		{ fieldname: "property", label: __("Property"), fieldtype: "Link", options: "Property" },
		{
			fieldname: "unit",
			label: __("Unit"),
			fieldtype: "Link",
			options: "Real Estate Unit",
			get_query() {
				const property = frappe.query_report.get_filter_value("property");
				return property ? { filters: { property } } : {};
			},
		},
		{
			fieldname: "lease_contract",
			label: __("Lease Contract"),
			fieldtype: "Link",
			options: "Lease Contract",
			get_query() {
				const customer = frappe.query_report.get_filter_value("customer");
				return customer ? { filters: { customer } } : {};
			},
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Highlight the running balance: red when the customer owes (positive debit balance).
		if (column.fieldname === "balance" && data && flt(data.balance) > 0) {
			value = `<span style="color:var(--bnd-danger,#DC2626);font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
