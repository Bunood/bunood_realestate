// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Customer Statement"] = {
	filters: [
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer", reqd: 1 },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
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
