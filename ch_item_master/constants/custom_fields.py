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
	# Location hierarchy: Company → City → Zone → Warehouses / Offices
	# ──────────────────────────────────────────────
	"Warehouse": [
		{
			"fieldname": "ch_location_section",
			"label": _("CH Location Hierarchy"),
			"fieldtype": "Section Break",
			"insert_after": "company",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_city",
			"label": _("City"),
			"fieldtype": "Link",
			"options": "CH City",
			"insert_after": "ch_location_section",
			"in_standard_filter": 1,
			"description": _("City this warehouse belongs to."),
		},
		{
			"fieldname": "ch_zone",
			"label": _("Zone"),
			"fieldtype": "Link",
			"options": "CH Store Zone",
			"insert_after": "ch_city",
			"in_standard_filter": 1,
			"description": _("Zone this warehouse belongs to."),
		},
		{
			"fieldname": "ch_location_type",
			"label": _("Location Type"),
			"fieldtype": "Select",
			"options": "\nStore Warehouse\nZone Warehouse\nTransit Warehouse\nService Warehouse\nStore Bin\nOther",
			"insert_after": "ch_zone",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_bin_type",
			"label": _("Bin Type"),
			"fieldtype": "Select",
			"options": "\nSellable\nIn-Transit\nDamaged\nDisposed\nReserved\nBuyback",
			"insert_after": "ch_location_type",
			"in_standard_filter": 1,
			"depends_on": "eval:doc.ch_location_type=='Store Bin'",
			"description": _("Stock state bucket for the parent Store Warehouse."),
		},
		{
			"fieldname": "ch_store",
			"label": _("CH Store"),
			"fieldtype": "Link",
			"options": "CH Store",
			"insert_after": "ch_bin_type",
			"in_standard_filter": 1,
			"description": _("Store this warehouse / bin belongs to."),
		},
	],
	"Branch": [
		{
			"fieldname": "ch_location_section",
			"label": _("CH Location Hierarchy"),
			"fieldtype": "Section Break",
			"insert_after": "branch",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "ch_location_section",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_city",
			"label": _("City"),
			"fieldtype": "Link",
			"options": "CH City",
			"insert_after": "ch_company",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_zone",
			"label": _("Zone"),
			"fieldtype": "Link",
			"options": "CH Store Zone",
			"insert_after": "ch_city",
			"in_standard_filter": 1,
			"description": _("Zone this office/branch belongs to."),
		},
	],
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
			"fieldname": "ch_disabled",
			"label": _("Disabled"),
			"fieldtype": "Check",
			"insert_after": "manufacturer_id",
			"default": "0",
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
			"fieldname": "ch_disabled",
			"label": _("Disabled"),
			"fieldtype": "Check",
			"insert_after": "brand_id",
			"default": "0",
			"in_list_view": 1,
		},
		{
			"fieldname": "ch_parent_brand",
			"label": _("Parent Brand"),
			"fieldtype": "Link",
			"options": "Brand",
			"insert_after": "ch_disabled",
			"in_list_view": 1,
			"description": _("If this is a sub-brand/alias (e.g. Galaxy is a sub-brand of Samsung), set the parent here. Sub-brands are hidden from kiosk but included in model search."),
		},
		{
			"fieldname": "ch_manufacturers_section",
			"label": _("Manufacturers"),
			"fieldtype": "Section Break",
			"insert_after": "ch_parent_brand",
			"description": _("List all manufacturers that produce items under this brand. "
			                  "E.g. Apple brand may be manufactured by Apple Inc. (OEM) "
			                  "and also by third-party accessory makers."),
		},
		{
			"fieldname": "ch_manufacturers",
			"label": _("Manufacturers"),
			"fieldtype": "Table",
			"options": "CH Brand Manufacturer",
			"insert_after": "ch_manufacturers_section",
			"reqd": 1,
			"description": _("At least one manufacturer is required."),
		},
	],
	# ──────────────────────────────────────────────
	# Item Group: Add disabled + integer ID for CH master hierarchy
	# ──────────────────────────────────────────────
	"Item Group": [
		{
			"fieldname": "item_group_id",
			"label": _("Item Group ID"),
			"fieldtype": "Int",
			"insert_after": "name",
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"bold": 1,
			"description": _("Auto-generated sequential ID for mobile/API integration"),
		},
		{
			"fieldname": "ch_disabled",
			"label": _("Disabled"),
			"fieldtype": "Check",
			"insert_after": "is_group",
			"default": "0",
			"in_list_view": 1,
			"in_standard_filter": 1,
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
			"insert_after": "naming_series",
			"collapsible": 0,
		},
		{
			"fieldname": "ch_category",
			"label": _("Category"),
			"fieldtype": "Link",
			"options": "CH Category",
			"insert_after": "ch_item_master_section",
			"in_standard_filter": 1,
			"reqd": 1,
			"read_only_depends_on": "eval:!doc.__islocal",
		},
		{
			"fieldname": "ch_sub_category",
			"label": _("Sub Category"),
			"fieldtype": "Link",
			"options": "CH Sub Category",
			"insert_after": "ch_category",
			"in_standard_filter": 1,
			"reqd": 1,
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
			# ch_model is NOT universally required at the meta level because
			# ERPNext may have non-CH items. Conditional enforcement is handled
			# server-side by ch_item_master.overrides.item._apply_subcategory_defaults().
			"reqd": 0,
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
			"fieldname": "ch_features_section",
			"label": _("Model Features"),
			"fieldtype": "Section Break",
			"insert_after": "ch_spec_values",
			"collapsible": 1,
			"description": _("Descriptive features inherited from the model (display, camera, platform, etc.)"),
		},
		{
			"fieldname": "ch_model_features",
			"label": _("Model Features"),
			"fieldtype": "Table",
			"options": "CH Item Feature",
			"insert_after": "ch_features_section",
			"read_only": 1,
			"description": _("Auto-populated from CH Model. Edit at model level."),
		},
		{
			"fieldname": "ch_master_ids_section",
			"label": _("Master IDs"),
			"fieldtype": "Section Break",
			"insert_after": "ch_model_features",
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
		{
			"fieldname": "ch_category_id",
			"label": _("Category ID"),
			"fieldtype": "Int",
			"insert_after": "ch_model_id",
			"read_only": 1,
			"no_copy": 1,
			"description": _("Copied from CH Category.category_id on save"),
		},
		{
			"fieldname": "ch_item_group_id",
			"label": _("Item Group ID"),
			"fieldtype": "Int",
			"insert_after": "ch_category_id",
			"read_only": 1,
			"no_copy": 1,
			"description": _("Copied from Item Group.item_group_id on save"),
		},
		# ──────────────────────────────────────────────
		# Warranty & Lifecycle
		# ──────────────────────────────────────────────
		{
			"fieldname": "ch_warranty_section",
			"label": _("Warranty & Lifecycle"),
			"fieldtype": "Section Break",
			"insert_after": "ch_item_group_id",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_default_warranty_months",
			"label": _("Default Warranty (Months)"),
			"fieldtype": "Int",
			"insert_after": "ch_warranty_section",
			"description": _("Default warranty duration in months for this item. Used when auto-creating Sold Plans on sale."),
		},
		{
			"fieldname": "ch_default_warranty_plan",
			"label": _("Default Warranty Plan"),
			"fieldtype": "Link",
			"options": "CH Warranty Plan",
			"insert_after": "ch_default_warranty_months",
			"description": _("The default warranty plan automatically issued when this item is sold."),
		},
		{
			"fieldname": "ch_warranty_col_break",
			"fieldtype": "Column Break",
			"insert_after": "ch_default_warranty_plan",
		},
		{
			"fieldname": "ch_lifecycle_status",
			"label": _("Lifecycle Status"),
			"fieldtype": "Select",
			"options": "Draft\nPending Review\nActive\nObsolete\nBlocked",
			"default": "Draft",
			"insert_after": "ch_warranty_col_break",
			"in_standard_filter": 1,
			"in_list_view": 1,
			"description": _("Governance lifecycle (Tier A). Items must be Active to be used in transactions. Drives the Item Master Workflow."),
		},
		{
			"fieldname": "ch_item_type",
			"label": _("Item Type"),
			"fieldtype": "Select",
			"options": "\nNew\nRefurbished\nPre-Owned\nDisplay\nDemo",
			"insert_after": "ch_lifecycle_status",
			"in_standard_filter": 1,
			"description": _("Type of item condition: New, Refurbished, Pre-Owned, Display/Demo."),
		},
		# ──────────────────────────────────────────────
		# Serial Number Profile (SAP MARC-SERNP / Oracle serial_number_control_code)
		#
		# CLASSIFICATION ONLY — does NOT toggle serial tracking.
		#   • Item.has_serial_no    →  WHETHER serials are tracked (1=yes / 0=no)
		#   • Item.ch_serial_kind   →  HOW serials are sourced (IMEI / Barcode)
		#
		# Used by:
		#   • Purchase Receipt    → stamps Serial No.ch_is_imei correctly on GRN
		#   • POS picker          → renders correct label ("Select IMEI" vs
		#                            "Select Serial / Barcode")
		#   • IMEI Tracker hub    → filters Real IMEI vs system Barcode serials
		#
		# Set automatically by the v3 unify-serial-kind patch from the item's
		# CH Category / Item Group ("Mobiles" → IMEI, else Barcode for any
		# has_serial_no=1 item). Editable by Item Master maintainers.
		# ──────────────────────────────────────────────
		{
			"fieldname": "ch_serial_kind",
			"label": _("Serial Number Kind"),
			"fieldtype": "Select",
			"options": "\nIMEI\nBarcode\nOthers",
			"insert_after": "ch_item_type",
			"in_standard_filter": 1,
			"depends_on": "eval:doc.has_serial_no",
			"description": _(
				"SAP Serial Number Profile / Oracle serial_number_control_code equivalent. "
				"Classifies HOW serials are sourced for this item:\n"
				"• IMEI – real 15-digit manufacturer IMEIs scanned at GRN "
				"(mobile phones, cellular watches).\n"
				"• Barcode – system-generated barcode serials "
				"(accessories, non-cellular devices, peripherals).\n"
				"• Others – externally supplied serials that are neither real "
				"IMEIs nor system-generated barcodes (e.g. vendor part serials, "
				"refurbished pool serials, special-handling SKUs).\n"
				"Leave blank for non-serialised items. "
				"This is the SINGLE source of truth used by Purchase Receipt, POS, "
				"and IMEI Tracker — do NOT mutate Item.has_serial_no at transaction time."
			),
		},
		{
			"fieldname": "ch_minimum_selling_price",
			"label": _("Minimum Selling Price (MSP)"),
			"fieldtype": "Currency",
			"insert_after": "ch_lifecycle_status",
			"permlevel": 1,
			"description": _("Floor price below which this item cannot be sold without approval."),
		},
		{
			"fieldname": "ch_msp_effective_from",
			"label": _("MSP Effective From"),
			"fieldtype": "Date",
			"insert_after": "ch_minimum_selling_price",
			"permlevel": 1,
			"description": _("Date from which this MSP applies."),
		},
		{
			"fieldname": "ch_allow_zero_rate",
			"label": _("Allow Zero Rate"),
			"fieldtype": "Check",
			"insert_after": "ch_msp_effective_from",
			"description": _("Allow this item to be sold at ₹0 in POS (e.g. bags, carry items)."),
		},
		# ── Tier B: Standard Cost ────────────────────────────────────────────
		{
			"fieldname": "ch_standard_cost",
			"label": _("Standard Cost"),
			"fieldtype": "Currency",
			"insert_after": "ch_allow_zero_rate",
			"permlevel": 1,
			"description": _("SAP-style standard (frozen) cost. Changes are audited automatically."),
		},
		{
			"fieldname": "ch_standard_cost_updated_on",
			"label": _("Standard Cost Updated On"),
			"fieldtype": "Date",
			"insert_after": "ch_standard_cost",
			"read_only": 1,
			"permlevel": 1,
			"description": _("Date of last standard cost change."),
		},
		# ── Tier B: Expiry Enforcement ───────────────────────────────────────
		{
			"fieldname": "ch_enforce_expiry",
			"label": _("Enforce Expiry on Delivery"),
			"fieldtype": "Check",
			"insert_after": "ch_standard_cost_updated_on",
			"description": _("Block delivery of expired batches for this item."),
		},
		# ── Tier B: Item Substitutes & Cross-References ──────────────────────
		{
			"fieldname": "ch_substitutes_section",
			"label": _("Substitutes & Cross-References"),
			"fieldtype": "Section Break",
			"insert_after": "ch_enforce_expiry",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_item_substitutes",
			"label": _("Item Substitutes & Cross-References"),
			"fieldtype": "Table",
			"insert_after": "ch_substitutes_section",
			"options": "CH Item Substitute",
			"description": _("Alternate items, supersessions, and cross-reference part numbers."),
		},
		# ── Tier B: Regulatory / Country HS Codes ───────────────────────────
		{
			"fieldname": "ch_regulatory_section",
			"label": _("Regulatory & Compliance"),
			"fieldtype": "Section Break",
			"insert_after": "ch_item_substitutes",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_country_hs_codes",
			"label": _("Country HS Codes"),
			"fieldtype": "Table",
			"insert_after": "ch_regulatory_section",
			"options": "CH Item Country HS",
			"description": _("Per-country tariff classification codes for cross-border compliance."),
		},
		# ── Tier C: GTIN / EAN / UPC ────────────────────────────────────────
		{
			"fieldname": "ch_gtin",
			"label": _("GTIN / EAN / UPC"),
			"fieldtype": "Data",
			"insert_after": "ch_country_hs_codes",			"permlevel": 1,			"in_standard_filter": 1,
			"description": _("Global Trade Item Number (EAN-13, UPC-A, EAN-8) for cross-border and retail barcode sync."),
		},
		# ── Tier C: Trading Partner Aliases ─────────────────────────────────
		{
			"fieldname": "ch_trading_partner_section",
			"label": _("Trading Partner Aliases"),
			"fieldtype": "Section Break",
			"insert_after": "ch_gtin",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_trading_partner_aliases",
			"label": _("Trading Partner Item Aliases"),
			"fieldtype": "Table",
			"insert_after": "ch_trading_partner_section",
			"options": "CH Item Trading Partner Alias",
			"description": _("Maps this item to supplier/customer-side item codes for EDI and cross-system sync."),
		},
		# ── Tier C: MRP / Coverage Planning ─────────────────────────────────
		{
			"fieldname": "ch_mrp_section",
			"label": _("MRP / Coverage Planning"),
			"fieldtype": "Section Break",
			"insert_after": "ch_trading_partner_aliases",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_mrp_type",
			"label": _("MRP Type"),
			"fieldtype": "Select",
			"options": "\nReorder Point\nFixed Lot\nDynamic",
			"insert_after": "ch_mrp_section",
			"description": _("Planning strategy: Reorder Point = threshold-based; Fixed Lot = fixed replenishment; Dynamic = demand-driven."),
		},
		{
			"fieldname": "ch_reorder_point",
			"label": _("Global Reorder Point"),
			"fieldtype": "Float",
			"insert_after": "ch_mrp_type",
			"default": "0",
			"description": _("Stock level that triggers a replenishment request. Overridden per-site in Site Defaults."),
		},
		{
			"fieldname": "ch_safety_stock_days",
			"label": _("Safety Stock (Days)"),
			"fieldtype": "Int",
			"insert_after": "ch_reorder_point",
			"default": "0",
			"description": _("Number of days of demand to keep as safety buffer."),
		},
		{
			"fieldname": "ch_mrp_col_break",
			"fieldtype": "Column Break",
			"insert_after": "ch_safety_stock_days",
		},
		{
			"fieldname": "ch_procurement_lead_days",
			"label": _("Procurement Lead Days"),
			"fieldtype": "Int",
			"insert_after": "ch_mrp_col_break",
			"default": "0",
			"description": _("Expected supplier lead time in days for MRP calculations."),
		},
		{
			"fieldname": "ch_lot_size",
			"label": _("Lot / Order Size"),
			"fieldtype": "Float",
			"insert_after": "ch_procurement_lead_days",
			"default": "0",
			"description": _("Fixed order quantity (lot size) for Fixed Lot MRP type."),
		},
		# ── Tier C: Site-Level Defaults ─────────────────────────────────────
		{
			"fieldname": "ch_site_defaults_section",
			"label": _("Site-Level Defaults"),
			"fieldtype": "Section Break",
			"insert_after": "ch_lot_size",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_site_defaults",
			"label": _("Site / Warehouse Defaults"),
			"fieldtype": "Table",
			"insert_after": "ch_site_defaults_section",
			"options": "CH Item Site Default",
			"description": _("Per-site reorder point, safety stock, lead time, and default UOM overrides."),
		},
		# ── Tier C: Full PLM State Machine ──────────────────────────────────
		{
			"fieldname": "ch_plm_section",
			"label": _("PLM Status"),
			"fieldtype": "Section Break",
			"insert_after": "ch_site_defaults",
			"collapsible": 1,
		},
		{
			"fieldname": "ch_plm_status",
			"label": _("PLM Status"),
			"fieldtype": "Select",
			"options": "NPI\nUnder Review\nSample Testing\nApproved\nActive Production\nEnd of Life\nDiscontinued",
			"default": "NPI",
			"insert_after": "ch_plm_section",
			"in_standard_filter": 1,
			"in_list_view": 0,
			"description": _("Full Product Lifecycle Management state. Controls purchase, sale, and production eligibility."),
		},
		{
			"fieldname": "ch_plm_changed_on",
			"label": _("PLM Status Changed On"),
			"fieldtype": "Datetime",
			"insert_after": "ch_plm_status",
			"read_only": 1,
		},
		{
			"fieldname": "ch_plm_col_break",
			"fieldtype": "Column Break",
			"insert_after": "ch_plm_changed_on",
		},
		{
			"fieldname": "ch_approval_status",
			"label": _("Model Approval Status"),
			"fieldtype": "Select",
			"options": "Draft\nSubmitted for Review\nApproved\nRejected",
			"default": "Draft",
			"insert_after": "ch_plm_col_break",
			"in_standard_filter": 1,
			"description": _("Formal model approval gate — item must be Approved before lifecycle can be set Active."),
		},
		{
			"fieldname": "ch_approval_date",
			"label": _("Approval Date"),
			"fieldtype": "Date",
			"insert_after": "ch_approval_status",
			"read_only": 1,
		},
		{
			"fieldname": "ch_approval_remarks",
			"label": _("Approval Remarks"),
			"fieldtype": "Small Text",
			"insert_after": "ch_approval_date",
		},
		# ── RBAC: Maker-Checker SoD tracking ───────────────────────────────────
		{
			"fieldname": "ch_submitted_by",
			"label": _("Submitted By"),
			"fieldtype": "Link",
			"options": "User",
			"insert_after": "ch_approval_remarks",
			"read_only": 1,
			"description": _("The user who submitted this item for review. Used for SoD enforcement."),
		},
		{
			"fieldname": "ch_submitted_on",
			"label": _("Submitted On"),
			"fieldtype": "Datetime",
			"insert_after": "ch_submitted_by",
			"read_only": 1,
			"description": _("Timestamp when the item was submitted for review."),
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
	# Serial No: per-instance classification (SAP/Oracle/Microsoft parity)
	#
	# `ch_serial_kind` is the AUTHORITATIVE per-serial classification field,
	# stamped from `Item.ch_serial_kind` at Purchase Receipt time. It is the
	# single source of truth for downstream consumers (IMEI Tracker, POS
	# serial picker, label printer, exports).
	#
	# `ch_is_imei` is a DERIVED compatibility boolean — kept for backward
	# compatibility with legacy queries / dashboards. Modern code MUST read
	# `ch_serial_kind` directly to differentiate IMEI vs Barcode vs Others.
	# ──────────────────────────────────────────────
	"Serial No": [
		{
			"fieldname": "ch_serial_kind",
			"label": _("Serial Number Kind"),
			"fieldtype": "Select",
			"options": "\nIMEI\nBarcode\nOthers",
			"insert_after": "warranty_expiry_date",
			"in_standard_filter": 1,
			"in_list_view": 1,
			"description": _(
				"Per-serial classification — stamped from Item.ch_serial_kind on GRN. "
				"Authoritative source for IMEI Tracker, POS, label printer, exports."
			),
		},
		{
			"fieldname": "ch_is_imei",
			"label": _("Is Real IMEI"),
			"fieldtype": "Check",
			"insert_after": "ch_serial_kind",
			"default": "0",
			"in_standard_filter": 1,
			"in_list_view": 1,
			"description": _(
				"DERIVED compatibility flag. = 1 only when ch_serial_kind = 'IMEI'. "
				"Modern code should read ch_serial_kind directly to distinguish "
				"IMEI vs Barcode vs Others."
			),
		},
		{
			"fieldname": "custom_barcode_series",
			"label": _("Barcode Series"),
			"fieldtype": "Data",
			"insert_after": "ch_is_imei",
			"description": _(
				"Auto-generated barcode series captured at Purchase Receipt time "
				"for non-IMEI serialised items (ch_serial_kind = Barcode)."
			),
		},
	],
	# ──────────────────────────────────────────────
	# Stock Entry: bin transfer metadata (reason + bin types + store)
	# ──────────────────────────────────────────────
	"Stock Entry": [
		{
			"fieldname": "ch_bin_transfer_section",
			"label": _("CH Bin Transfer"),
			"fieldtype": "Section Break",
			"insert_after": "remarks",
			"collapsible": 1,
			"description": _("Populated when this Stock Entry is a bin-to-bin transfer raised from POS or backend."),
		},
		{
			"fieldname": "ch_store",
			"label": _("CH Store"),
			"fieldtype": "Link",
			"options": "CH Store",
			"insert_after": "ch_bin_transfer_section",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_from_bin_type",
			"label": _("From Bin Type"),
			"fieldtype": "Select",
			"options": "\nSellable\nIn-Transit\nDamaged\nDisposed\nReserved\nBuyback",
			"insert_after": "ch_store",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_to_bin_type",
			"label": _("To Bin Type"),
			"fieldtype": "Select",
			"options": "\nSellable\nIn-Transit\nDamaged\nDisposed\nReserved\nBuyback",
			"insert_after": "ch_from_bin_type",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "ch_bin_transfer_reason",
			"label": _("Bin Transfer Reason"),
			"fieldtype": "Link",
			"options": "CH Bin Transfer Reason",
			"insert_after": "ch_to_bin_type",
			"in_standard_filter": 1,
		},
	],
}
