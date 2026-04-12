// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Brand Claim Tracker"] = {
	filters: [
		{
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
		}
	],
};
