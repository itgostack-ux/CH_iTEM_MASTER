// Copyright (c) 2026, GoStack and contributors
// Expiring Prices — Report filters

frappe.query_reports["Expiring Prices"] = {
	filters: [
		{
			fieldname: "days_ahead",
			label: __("Days Ahead"),
			fieldtype: "Int",
			default: 7,
			reqd: 1,
		},
		{
			fieldname: "channel",
			label: __("Channel"),
			fieldtype: "Link",
			options: "CH Price Channel",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
	],
};
