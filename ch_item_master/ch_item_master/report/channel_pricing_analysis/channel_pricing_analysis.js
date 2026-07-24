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
			on_change: (query_report) => {
				// Cascade: clear the sub category if it no longer belongs to the chosen category
				const cat = query_report.get_filter_value("category");
				const sub = query_report.get_filter_value("sub_category");
				if (cat && sub) {
					frappe.db.get_value("CH Sub Category", sub, "category").then((r) => {
						if (((r.message || {}).category || "") !== cat) {
							query_report.set_filter_value("sub_category", "");
						} else {
							query_report.refresh(true);
						}
					});
				} else {
					query_report.refresh(true);
				}
			},
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
