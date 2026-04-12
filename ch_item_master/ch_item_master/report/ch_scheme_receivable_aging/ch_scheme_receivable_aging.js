// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["CH Scheme Receivable Aging"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "overdue_only",
			label: __("Overdue Only"),
			fieldtype: "Data",
		},
		{
			fieldname: "party",
			label: __("Party"),
			fieldtype: "Data",
		},
		{
			fieldname: "scheme_type",
			label: __("Scheme Type"),
			fieldtype: "Data",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
		}
	],
};
