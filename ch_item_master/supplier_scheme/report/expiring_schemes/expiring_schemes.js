// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Expiring Schemes"] = {
	filters: [
		{
			fieldname: "days_threshold",
			label: __("Days Threshold"),
			fieldtype: "Data",
		}
	],
};
