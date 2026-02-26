// Copyright (c) 2026, GoStack and contributors
// Category Manager Report — Filters

frappe.query_reports["Category Manager Report"] = {
	filters: [
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Link",
			options: "CH Category",
		},
		{
			fieldname: "sub_category",
			label: __("Sub Category"),
			fieldtype: "Link",
			options: "CH Sub Category",
			get_query() {
				let cat = frappe.query_report.get_filter_value("category");
				return cat ? { filters: { category: cat } } : {};
			},
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "channel",
			label: __("Channel"),
			fieldtype: "Link",
			options: "CH Price Channel",
		},
	],
};
