// Copyright (c) 2026, GoStack and contributors
// Price Comparison Across Channels — Report filters

frappe.query_reports["Price Comparison Across Channels"] = {
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
		},
	],
};
