// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Pending Compliance"] = {
	filters: [
		{
			fieldname: "scheme",
			label: __("Scheme"),
			fieldtype: "Data",
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
		}
	],
};
