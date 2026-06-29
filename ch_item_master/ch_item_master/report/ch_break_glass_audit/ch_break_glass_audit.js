// Copyright (c) 2026, GoStack and contributors
// CH Break Glass Audit — Report filters

frappe.query_reports["CH Break Glass Audit"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "user",
			label: __("User"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "status",
			label: __("Session Status"),
			fieldtype: "Select",
			options: ["All", "Open", "Closed"].join("\n"),
			default: "All",
		},
		{
			fieldname: "review_status",
			label: __("Review Status"),
			fieldtype: "Select",
			options: ["All", "Pending Review", "Reviewed", "Escalated"].join("\n"),
			default: "All",
		},
		{
			fieldname: "sla_hours",
			label: __("SLA Target (Hours)"),
			fieldtype: "Int",
			default: 4,
			description: __("Sessions open longer than this are flagged as SLA Breach."),
		},
		{
			fieldname: "sla_breach",
			label: __("Show only SLA breaches"),
			fieldtype: "Check",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		const html = default_formatter(value, row, column, data);
		if (column.fieldname === "sla_bucket" && data && data.sla_bucket) {
			const palette = {
				"Within Target": "#15803d",
				"Breach": "#b45309",
				"Hard Breach": "#b91c1c",
			};
			const bg = {
				"Within Target": "#dcfce7",
				"Breach": "#fef3c7",
				"Hard Breach": "#fee2e2",
			};
			const colour = palette[data.sla_bucket] || "#475569";
			const back = bg[data.sla_bucket] || "#e2e8f0";
			return `<span style="background:${back};color:${colour};padding:2px 8px;border-radius:4px;font-weight:600">${data.sla_bucket}</span>`;
		}
		return html;
	},
};
