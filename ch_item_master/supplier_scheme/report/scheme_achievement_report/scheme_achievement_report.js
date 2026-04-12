// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Scheme Achievement Report"] = {
	filters: [
		{
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "scheme",
			label: __("Scheme"),
			fieldtype: "Data",
		},
		{
			fieldname: "show_reversed",
			label: __("Show Reversed"),
			fieldtype: "Data",
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
		}
	],
};
