// Copyright (c) 2026, Bunood and contributors
// For license information, please see license.txt

frappe.query_reports["Expiring Leases"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "days", label: __("Within Days"), fieldtype: "Int", default: 60 },
		{ fieldname: "auto_renew_only", label: __("Auto-renew only"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Red-flag leases expiring within 7 days that have no renewal draft yet.
		if (column.fieldname === "days_left" && data && flt(data.days_left) <= 7 && !data.renewal_drafted) {
			value = `<span style="color:var(--bnd-danger,#DC2626);font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
