// Copyright (c) 2026, GoStack and contributors
// Pricing Status by Company — Filters

frappe.query_reports["Pricing Status by Company"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "channel",
			label: __("Channel"),
			fieldtype: "Link",
			options: "CH Price Channel",
		},
		{
			fieldname: "status",
			label: __("Price Status"),
			fieldtype: "Select",
			options: "\nDraft\nActive\nScheduled\nExpired",
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Link",
			options: "CH Category",
		},
	],
};
