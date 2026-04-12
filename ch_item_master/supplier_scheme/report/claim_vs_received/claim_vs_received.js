// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Claim vs Received"] = {
	filters: [
		{
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
		},
		{
			fieldname: "claim_status",
			label: __("Claim Status"),
			fieldtype: "Data",
		}
	],
};
