// Copyright (c) 2026, GoStack and contributors
// Items Without Active Price — Report filters

frappe.query_reports["Items Without Active Price"] = {
	filters: [
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Link",
			options: "CH Category",
			on_change: (query_report) => {
				// Cascade: clear sub category / model if they no longer belong to the chosen category
				const cat = query_report.get_filter_value("category");
				const sub = query_report.get_filter_value("sub_category");
				if (cat && sub) {
					frappe.db.get_value("CH Sub Category", sub, "category").then((r) => {
						if (((r.message || {}).category || "") !== cat) {
							query_report.set_filter_value({ sub_category: "", model: "" });
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
			on_change: (query_report) => {
				if (query_report._no_refresh) return;
				// Cascade: clear the model if it no longer belongs to the chosen sub category
				const sub = query_report.get_filter_value("sub_category");
				const model = query_report.get_filter_value("model");
				if (sub && model) {
					frappe.db.get_value("CH Model", model, "sub_category").then((r) => {
						if (((r.message || {}).sub_category || "") !== sub) {
							query_report.set_filter_value("model", "");
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
			fieldname: "model",
			label: __("Model"),
			fieldtype: "Link",
			options: "CH Model",
			get_query() {
				const filters = {};
				const sc = frappe.query_report.get_filter_value("sub_category");
				const cat = frappe.query_report.get_filter_value("category");
				if (sc) filters.sub_category = sc;
				else if (cat) filters.category = cat;
				return {
					query: "ch_item_master.ch_item_master.api.search_ch_models",
					filters: filters,
				};
			},
		},
	],
};
