app_name = "ch_item_master"
app_title = "CH Item Master"
app_publisher = "GoStack"
app_description = "Common Item Master"
app_email = "contact@gostack.in"
app_license = "custom"

boot_session = "ch_item_master.boot.boot_session"
required_apps = ["frappe/erpnext"]

add_to_apps_screen = [
	{
		"name": "ch_item_master",
		"logo": "/assets/ch_item_master/images/icon.svg",
		"title": "CH Item Master",
		"route": "/desk/ch-item-master",
		"has_permission": "ch_item_master.ch_item_master.utils.check_app_permission",
	}
]

app_include_js = "/assets/ch_item_master/js/item_quick_entry.js"
doctype_js = {"Item": "public/js/item.js", "Customer": "public/js/customer.js"}

# Installation / Migration
after_install = "ch_item_master.install.after_install"
after_migrate = [
	"ch_item_master.setup.setup_roles",
	"ch_item_master.setup.create_ch_custom_fields",
	"ch_item_master.setup.setup_channels",
	"ch_item_master.setup.sync_workspace",
	"ch_item_master.setup.setup_vas_settings",
	"ch_item_master.ch_core.location_hierarchy.backfill_location_hierarchy",
	"ch_item_master.ch_item_master.backfill_ids.backfill_ids_after_migrate",
	"ch_item_master.seed_status_registry.validate_status_registry",
	"ch_item_master.ch_item_master.page.imei_tracker.imei_tracker_api.backfill_is_imei_flag",
]
before_uninstall = "ch_item_master.install.before_uninstall"

# Dashboard overrides
override_doctype_dashboards = {
	"Customer": "ch_item_master.ch_item_master.overrides.customer_dashboard.get_data",
}

# Scheduled Tasks
scheduler_events = {
	"daily_long": [
		"ch_item_master.ch_item_master.scheduled_tasks.auto_expire_records",
		"ch_item_master.ch_customer_master.doctype.ch_loyalty_transaction.ch_loyalty_transaction.expire_loyalty_points",
		"ch_item_master.ch_item_master.commercial_api.run_channel_parity_check",
		"ch_item_master.ch_item_master.commercial_api.run_tag_auto_repricing",
		"ch_item_master.ch_item_master.voucher_api.expire_vouchers",
		"ch_item_master.ch_item_master.doctype.ch_coupon_campaign.ch_coupon_campaign.expire_campaigns",
		"ch_item_master.supplier_scheme.scheduled.auto_close_expired_schemes",
		"ch_item_master.supplier_scheme.scheduled.send_expiry_claim_reminders",
	],
	"hourly": [
		"ch_item_master.ch_item_master.exception_api.expire_stale_exceptions",
	],
	"weekly": [
		"ch_item_master.ch_item_master.doctype.ch_scheme_receivable.ch_scheme_receivable.run_scheduled_dunning",
	],
}

# Company-wise data security
permission_query_conditions = {
	"CH Warranty Claim": "ch_item_master.security.get_warranty_claim_query",
	"CH Sold Plan": "ch_item_master.security.get_sold_plan_query",
	"CH Customer Device": "ch_item_master.security.get_customer_device_query",
	"CH Warranty Plan": "ch_item_master.security.get_warranty_plan_query",
	"CH Exception Request": "ch_item_master.security.get_exception_request_query",
}

has_permission = {
	"CH Warranty Claim": "ch_item_master.security.has_warranty_claim_permission",
	"CH Sold Plan": "ch_item_master.security.has_sold_plan_permission",
	"CH Customer Device": "ch_item_master.security.has_customer_device_permission",
	"CH Warranty Plan": "ch_item_master.security.has_warranty_plan_permission",
	"CH Exception Request": "ch_item_master.security.has_exception_request_permission",
}

# Document Events
doc_events = {
	"Item": {
		"before_insert": "ch_item_master.ch_item_master.overrides.item.before_insert",
		"before_save": "ch_item_master.ch_item_master.overrides.item.before_save",
	},
	"Manufacturer": {
		"before_insert": "ch_item_master.ch_item_master.overrides.manufacturer.before_insert",
		"before_save": "ch_item_master.ch_item_master.overrides.manufacturer.before_save",
	},
	"Brand": {
		"before_insert": "ch_item_master.ch_item_master.overrides.brand.before_insert",
		"before_save": "ch_item_master.ch_item_master.overrides.brand.before_save",
	},
	"Item Group": {
		"before_insert": "ch_item_master.ch_item_master.overrides.item_group.before_insert",
	},
	"Customer": {
		"before_insert": "ch_item_master.ch_customer_master.overrides.customer.before_insert",
		"validate": "ch_item_master.ch_customer_master.overrides.customer.validate",
	},
	"Sales Invoice": {
		"on_submit": [
			"ch_item_master.ch_customer_master.hooks.on_sales_invoice_submit",
			"ch_item_master.ch_item_master.doctype.ch_scheme_receivable.ch_scheme_receivable.create_from_pos_invoice",
			"ch_item_master.supplier_scheme.engine.process_invoice_items",
		],
		"on_cancel": "ch_item_master.supplier_scheme.engine.reverse_invoice_items",
	},
	"POS Invoice": {
		"on_submit": [
			"ch_item_master.ch_item_master.doctype.ch_scheme_receivable.ch_scheme_receivable.create_from_pos_invoice",
			"ch_item_master.supplier_scheme.engine.process_invoice_items",
		],
		"on_cancel": "ch_item_master.supplier_scheme.engine.reverse_invoice_items",
	},
	"Purchase Receipt": {
		"on_submit": "ch_item_master.ch_item_master.overrides.purchase_receipt.on_submit",
		"on_cancel": "ch_item_master.ch_item_master.overrides.purchase_receipt.on_cancel",
	},
	"CH Warranty Claim": {
		"on_submit": "ch_item_master.ch_item_master.integrations.warranty_claim_hooks.on_submit",
		"on_cancel": "ch_item_master.ch_item_master.integrations.warranty_claim_hooks.on_cancel",
	},
	"Service Request": {
		"on_submit": "ch_item_master.ch_customer_master.hooks.on_service_request_submit",
		"on_update": "ch_item_master.ch_item_master.integrations.gofix_integration.on_service_request_update",
	},
	"Buyback Assessment": {
		"on_update": "ch_item_master.ch_customer_master.hooks.on_buyback_assessment_update",
	},
	# Cache invalidation for active-scheme lookup (perf: avoids per-invoice DB scan)
	"Supplier Scheme Circular": {
		"on_update": "ch_item_master.supplier_scheme.engine.invalidate_active_schemes_cache",
		"on_submit": "ch_item_master.supplier_scheme.engine.invalidate_active_schemes_cache",
		"on_cancel": "ch_item_master.supplier_scheme.engine.invalidate_active_schemes_cache",
		"on_trash": "ch_item_master.supplier_scheme.engine.invalidate_active_schemes_cache",
	},
	"CH Sold Plan": {
		"on_submit": "ch_item_master.ch_customer_master.hooks.on_sold_plan_change",
		"on_cancel": "ch_item_master.ch_customer_master.hooks.on_sold_plan_change",
		"on_update": "ch_item_master.ch_customer_master.hooks.on_sold_plan_change",
	},
	# Transactions: manual rate-filling removed.
	# ERPNext natively uses Item Price (fetched by price list) and applies Pricing Rules.
	# CH Item Price.on_update() syncs into ERPNext Item Price on save.
	# CH Item Offer.approve() syncs into ERPNext Pricing Rule on approval.
	# No manual hook required for Sales Order, Sales Invoice, Quotation, etc.

	# ── Price governance: block direct edits & auto-log changes ───────────
	"CH Item Price": {
		"validate": "ch_item_master.ch_item_master.price_governance.validate_ch_item_price",
		"on_update": "ch_item_master.ch_item_master.price_governance.log_ch_item_price_change",
	},
	"Buyback Price Master": {
		"validate": "ch_item_master.ch_item_master.price_governance.validate_buyback_price",
		"on_update": "ch_item_master.ch_item_master.price_governance.log_buyback_price_change",
	},
	# ── After Data Import: cascade denormalized IDs ────────────────────────
	"Data Import": {
		"on_update_after_submit": "ch_item_master.ch_item_master.backfill_ids.on_data_import_complete",
	},
}
