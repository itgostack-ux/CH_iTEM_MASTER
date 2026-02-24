# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

# Custom Fields added to ERPNext core doctypes by CH Item Master.
# Pattern follows India Compliance / HRMS approach:
#   - Keys are doctype names (or tuples for multiple doctypes)
#   - Values are lists of field definition dicts
#   - Applied via frappe.custom.doctype.custom_field.custom_field.create_custom_fields()

CUSTOM_FIELDS = {
	# ──────────────────────────────────────────────
	# Brand: Add manufacturer link
	# ──────────────────────────────────────────────
	"Brand": [
		{
			"fieldname": "ch_manufacturer",
			"label": "Manufacturer",
			"fieldtype": "Link",
			"options": "Manufacturer",
			"insert_after": "brand",
			"reqd": 1,
			"description": "The manufacturer this brand belongs to",
		},
	],
	# ──────────────────────────────────────────────
	# Item: Add CH Item Master hierarchy fields
	# ──────────────────────────────────────────────
	"Item": [
		{
			"fieldname": "ch_item_master_section",
			"label": "CH Item Master",
			"fieldtype": "Section Break",
			"insert_after": "item_group",
			"collapsible": 0,
		},
		{
			"fieldname": "ch_category",
			"label": "Category",
			"fieldtype": "Link",
			"options": "CH Category",
			"insert_after": "ch_item_master_section",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_sub_category",
			"label": "Sub Category",
			"fieldtype": "Link",
			"options": "CH Sub Category",
			"insert_after": "ch_category",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_column_break_01",
			"fieldtype": "Column Break",
			"insert_after": "ch_sub_category",
		},
		{
			"fieldname": "ch_model",
			"label": "Model",
			"fieldtype": "Link",
			"options": "CH Model",
			"insert_after": "ch_column_break_01",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_display_name",
			"label": "CH Display Name",
			"fieldtype": "Data",
			"read_only": 1,
			"insert_after": "ch_model",
			"description": "Auto-generated: Manufacturer + Model + Spec Values",
			"in_list_view": 1,
		},
		{
			"fieldname": "ch_spec_values_section",
			"label": "Specification Values",
			"fieldtype": "Section Break",
			"insert_after": "ch_display_name",
			"collapsible": 0,
			"description": "Select spec values that form the item name. Populated automatically when a Model is chosen.",
		},
		{
			"fieldname": "ch_spec_values",
			"label": "Spec Values",
			"fieldtype": "Table",
			"options": "CH Item Spec Value",
			"insert_after": "ch_spec_values_section",
		},
	],
	# ──────────────────────────────────────────────
	# Item Price: Add MRP and MOP alongside selling price
	# (price_list_rate = Selling Price — ERPNext standard)
	# ──────────────────────────────────────────────
	"Item Price": [
		{
			"fieldname": "ch_mrp",
			"label": "MRP",
			"fieldtype": "Currency",
			"insert_after": "price_list_rate",
			"description": "Maximum Retail Price – managed by CH Item Master",
		},
		{
			"fieldname": "ch_mop",
			"label": "MOP",
			"fieldtype": "Currency",
			"insert_after": "ch_mrp",
			"description": "Market Operating Price – managed by CH Item Master",
		},
		{
			"fieldname": "ch_source_price",
			"label": "CH Price Record",
			"fieldtype": "Link",
			"options": "CH Item Price",
			"insert_after": "ch_mop",
			"read_only": 1,
			"description": "The CH Item Price that last wrote this record",
		},
	],
	# ──────────────────────────────────────────────
	# Item Group: Link to CH Sub Category
	# ──────────────────────────────────────────────
	"Item Group": [
		{
			"fieldname": "ch_category",
			"label": "CH Category",
			"fieldtype": "Link",
			"options": "CH Category",
			"insert_after": "parent_item_group",
			"description": "Links this Item Group to a CH Item Master Category",
		},
	],
}
