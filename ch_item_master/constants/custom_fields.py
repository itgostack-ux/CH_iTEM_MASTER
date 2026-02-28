# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

# Custom Fields added to ERPNext core doctypes by CH Item Master.
# Pattern follows India Compliance / HRMS approach:
#   - Keys are doctype names (or tuples for multiple doctypes)
#   - Values are lists of field definition dicts
#   - Applied via frappe.custom.doctype.custom_field.custom_field.create_custom_fields()

from frappe import _

CUSTOM_FIELDS = {
	# ──────────────────────────────────────────────
	# Manufacturer: Add integer ID for mobile/API
	# ──────────────────────────────────────────────
	"Manufacturer": [
		{
			"fieldname": "manufacturer_id",
			"label": _("Manufacturer ID"),
			"fieldtype": "Int",
			"insert_after": "name",
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"description": _("Auto-generated sequential ID for mobile/API integration"),
		},
		{
			"fieldname": "ch_is_active",
			"label": _("Is Active"),
			"fieldtype": "Check",
			"insert_after": "manufacturer_id",
			"default": "1",
			"in_list_view": 1,
		},
	],
	# ──────────────────────────────────────────────
	# Brand: Add manufacturer link and integer ID
	# ──────────────────────────────────────────────
	"Brand": [
		{
			"fieldname": "brand_id",
			"label": _("Brand ID"),
			"fieldtype": "Int",
			"insert_after": "name",
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"description": _("Auto-generated sequential ID for mobile/API integration"),
		},
		{
			"fieldname": "ch_manufacturer",
			"label": _("Manufacturer"),
			"fieldtype": "Link",
			"options": "Manufacturer",
			"insert_after": "brand",
			"reqd": 1,
			"description": _("The manufacturer this brand belongs to"),
		},
		{
			"fieldname": "ch_is_active",
			"label": _("Is Active"),
			"fieldtype": "Check",
			"insert_after": "ch_manufacturer",
			"default": "1",
			"in_list_view": 1,
		},
	],
	# ──────────────────────────────────────────────
	# Item: Add CH Item Master hierarchy fields
	# ──────────────────────────────────────────────
	"Item": [
		{
			"fieldname": "ch_item_master_section",
			"label": _("CH Item Master"),
			"fieldtype": "Section Break",
			"insert_after": "item_group",
			"collapsible": 0,
		},
		{
			"fieldname": "ch_category",
			"label": _("Category"),
			"fieldtype": "Link",
			"options": "CH Category",
			"insert_after": "ch_item_master_section",
			"in_standard_filter": 1,
			"read_only_depends_on": "eval:!doc.__islocal",
		},
		{
			"fieldname": "ch_sub_category",
			"label": _("Sub Category"),
			"fieldtype": "Link",
			"options": "CH Sub Category",
			"insert_after": "ch_category",
			"in_standard_filter": 1,
			"read_only_depends_on": "eval:!doc.__islocal",
		},
		{
			"fieldname": "ch_column_break_01",
			"fieldtype": "Column Break",
			"insert_after": "ch_sub_category",
		},
		{
			"fieldname": "ch_model",
			"label": _("Model"),
			"fieldtype": "Link",
			"options": "CH Model",
			"insert_after": "ch_column_break_01",
			"in_standard_filter": 1,
			"read_only_depends_on": "eval:!doc.__islocal",
		},
		{
			"fieldname": "ch_display_name",
			"label": _("CH Display Name"),
			"fieldtype": "Data",
			"read_only": 1,
			"insert_after": "ch_model",
			"description": _("Auto-generated: Manufacturer + Model + Spec Values"),
			"in_list_view": 1,
		},
		{
			"fieldname": "ch_spec_values_section",
			"label": _("Specification Values"),
			"fieldtype": "Section Break",
			"insert_after": "ch_display_name",
			"collapsible": 0,
			"description": _("Select spec values that form the item name. Populated automatically when a Model is chosen."),
		},
		{
			"fieldname": "ch_spec_values",
			"label": _("Spec Values"),
			"fieldtype": "Table",
			"options": "CH Item Spec Value",
			"insert_after": "ch_spec_values_section",
		},
		{
			"fieldname": "ch_master_ids_section",
			"label": _("Master IDs"),
			"fieldtype": "Section Break",
			"insert_after": "ch_spec_values",
			"collapsible": 1,
			"description": _("Auto-populated numeric IDs from linked master records (for mobile/API)."),
		},
		{
			"fieldname": "ch_brand_id",
			"label": _("Brand ID"),
			"fieldtype": "Int",
			"insert_after": "ch_master_ids_section",
			"read_only": 1,
			"no_copy": 1,
			"description": _("Copied from Brand.brand_id on save"),
		},
		{
			"fieldname": "ch_manufacturer_id",
			"label": _("Manufacturer ID"),
			"fieldtype": "Int",
			"insert_after": "ch_brand_id",
			"read_only": 1,
			"no_copy": 1,
			"description": _("Copied from Manufacturer.manufacturer_id on save"),
		},
		{
			"fieldname": "ch_master_ids_col_break",
			"fieldtype": "Column Break",
			"insert_after": "ch_manufacturer_id",
		},
		{
			"fieldname": "ch_sub_category_id",
			"label": _("Sub Category ID"),
			"fieldtype": "Int",
			"insert_after": "ch_master_ids_col_break",
			"read_only": 1,
			"no_copy": 1,
			"description": _("Copied from CH Sub Category.sub_category_id on save"),
		},
		{
			"fieldname": "ch_model_id",
			"label": _("Model ID"),
			"fieldtype": "Int",
			"insert_after": "ch_sub_category_id",
			"read_only": 1,
			"no_copy": 1,
			"description": _("Copied from CH Model.model_id on save"),
		},
	],
	# ──────────────────────────────────────────────
	# Item Price: Add MRP and MOP alongside selling price
	# (price_list_rate = Selling Price — ERPNext standard)
	# ──────────────────────────────────────────────
	"Item Price": [
		{
			"fieldname": "ch_mrp",
			"label": _("MRP"),
			"fieldtype": "Currency",
			"insert_after": "price_list_rate",
			"description": _("Maximum Retail Price – managed by CH Item Master"),
		},
		{
			"fieldname": "ch_mop",
			"label": _("MOP"),
			"fieldtype": "Currency",
			"insert_after": "ch_mrp",
			"description": _("Market Operating Price – managed by CH Item Master"),
		},
		{
			"fieldname": "ch_source_price",
			"label": _("CH Price Record"),
			"fieldtype": "Link",
			"options": "CH Item Price",
			"insert_after": "ch_mop",
			"read_only": 1,
			"description": _("The CH Item Price that last wrote this record"),
		},
	],
	# ──────────────────────────────────────────────
	# Item Group: Link to CH Sub Category
	# ──────────────────────────────────────────────
	"Item Group": [
		{
			"fieldname": "ch_category",
			"label": _("CH Category"),
			"fieldtype": "Link",
			"options": "CH Category",
			"insert_after": "parent_item_group",
			"description": _("Links this Item Group to a CH Item Master Category"),
		},
		{
			"fieldname": "ch_category_id",
			"label": _("Category ID"),
			"fieldtype": "Int",
			"insert_after": "ch_category",
			"read_only": 1,
			"no_copy": 1,
			"description": _("Auto-populated from CH Category.category_id"),
		},
		{
			"fieldname": "ch_is_active",
			"label": _("Is Active"),
			"fieldtype": "Check",
			"insert_after": "ch_category_id",
			"default": "1",
			"in_list_view": 1,
		},
	],
}
