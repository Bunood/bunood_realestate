// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Expiring Documents"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "days", label: __("Within Days"), fieldtype: "Int", default: 90 },
		{
			fieldname: "document_type",
			label: __("Document Type"),
			fieldtype: "Link",
			options: "RE Document Type",
		},
		{ fieldname: "include_expired", label: __("Include Expired"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Red for already-expired, amber for due within the week.
		if (column.fieldname === "days_left" && data) {
			if (flt(data.days_left) < 0) {
				value = `<span style="color:var(--bnd-danger,#DC2626);font-weight:600;">${value}</span>`;
			} else if (flt(data.days_left) <= 7) {
				value = `<span style="color:var(--bnd-warning,#D97706);font-weight:600;">${value}</span>`;
			}
		}
		return value;
	},
};
