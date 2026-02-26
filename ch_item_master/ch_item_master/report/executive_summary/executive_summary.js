// Copyright (c) 2026, GoStack and contributors
// Executive Summary — Filters

frappe.query_reports["Executive Summary"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: "Company\nCategory\nChannel",
			default: "Company",
		},
	],
};
