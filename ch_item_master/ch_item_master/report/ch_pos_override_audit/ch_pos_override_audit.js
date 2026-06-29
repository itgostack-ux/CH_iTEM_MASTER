// Copyright (c) 2026, GoStack and contributors
// CH POS Override Audit — filter definitions + cell formatters.

frappe.query_reports["CH POS Override Audit"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.now_date(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "store_warehouse",
			label: __("Store / Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "pos_user",
			label: __("Cashier"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "override_type",
			label: __("Override Type"),
			fieldtype: "Select",
			options: ["", "Rate Override", "Discount Override", "Below MOP", "Additional Discount"].join("\n"),
		},
		{
			fieldname: "only_unapproved",
			label: __("Only Unapproved"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "approval_status" && data) {
			const approved = data.approval_status === "Approved";
			const color = approved ? "green" : "red";
			value = `<span class="indicator-pill ${color}">${data.approval_status}</span>`;
		}

		if (column.fieldname === "discount_percent" && data && data.discount_percent >= 25) {
			value = `<span style="color: var(--red-500); font-weight: 600;">${value}</span>`;
		}

		return value;
	},
};
