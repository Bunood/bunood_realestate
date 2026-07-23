// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Owner Statement"] = {
	filters: [
		{ fieldname: "owner", label: __("Owner (Supplier)"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "property", label: __("Property"), fieldtype: "Link", options: "Property" },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Emphasise the net paid to the owner.
		if (column.fieldname === "owner_payout" && data && flt(data.owner_payout) > 0) {
			value = `<span style="color:var(--bnd-primary,#1F5145);font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
