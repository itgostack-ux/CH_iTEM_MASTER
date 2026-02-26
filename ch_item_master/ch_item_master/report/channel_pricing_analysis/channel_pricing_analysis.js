// Copyright (c) 2026, GoStack and contributors
// Channel Pricing Analysis — Filters

frappe.query_reports["Channel Pricing Analysis"] = {
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
			fieldname: "manufacturer",
			label: __("Manufacturer"),
			fieldtype: "Link",
			options: "Manufacturer",
		},
	],
};
