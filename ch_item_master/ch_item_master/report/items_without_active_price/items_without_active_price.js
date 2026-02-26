// Copyright (c) 2026, GoStack and contributors
// Items Without Active Price — Report filters

frappe.query_reports["Items Without Active Price"] = {
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
			fieldname: "model",
			label: __("Model"),
			fieldtype: "Link",
			options: "CH Model",
			get_query() {
				let sc = frappe.query_report.get_filter_value("sub_category");
				return sc ? { filters: { sub_category: sc } } : {};
			},
		},
	],
};
