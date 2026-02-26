// Copyright (c) 2026, GoStack and contributors
// Model Coverage — Report filters

frappe.query_reports["Model Coverage"] = {
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
			fieldname: "show_inactive",
			label: __("Show Inactive Models"),
			fieldtype: "Check",
			default: 0,
		},
	],
};
