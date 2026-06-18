// Copyright (c) 2026, GoGizmo
// License: MIT

frappe.query_reports["Stores by Location"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
        },
        {
            fieldname: "state",
            label: __("State"),
            fieldtype: "Link",
            options: "CH State",
        },
        {
            fieldname: "city",
            label: __("City"),
            fieldtype: "Link",
            options: "CH City",
            get_query: function () {
                const state = frappe.query_report.get_filter_value("state");
                return { filters: state ? { state: state, disabled: 0 } : { disabled: 0 } };
            },
        },
        {
            fieldname: "zone",
            label: __("Zone"),
            fieldtype: "Link",
            options: "CH Store Zone",
            get_query: function () {
                const filters = {};
                const company = frappe.query_report.get_filter_value("company");
                const city = frappe.query_report.get_filter_value("city");
                if (company) filters.company = company;
                if (city) filters.city = city;
                return { filters };
            },
        },
        {
            fieldname: "capability",
            label: __("Capability"),
            fieldtype: "Select",
            options: ["", "Buyback", "Service", "Retail"].join("\n"),
        },
        {
            fieldname: "status",
            label: __("Status"),
            fieldtype: "Select",
            options: ["Active", "Disabled", "All"].join("\n"),
            default: "Active",
        },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;
        if (column.fieldname === "status") {
            const color = data.status === "Disabled" ? "red" : "green";
            return `<span class="indicator ${color}">${value}</span>`;
        }
        return value;
    },
};
