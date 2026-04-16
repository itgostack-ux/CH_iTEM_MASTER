frappe.query_reports["Supplier Price Comparison"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company"
        },
        {
            fieldname: "supplier",
            label: __("Supplier"),
            fieldtype: "Link",
            options: "Supplier"
        },
        {
            fieldname: "item_code",
            label: __("Item"),
            fieldtype: "Link",
            options: "Item"
        },
        {
            fieldname: "channel",
            label: __("Sell Channel"),
            fieldtype: "Link",
            options: "CH Price Channel",
            default: "POS"
        }
    ]
};
