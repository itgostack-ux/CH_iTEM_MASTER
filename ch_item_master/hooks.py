app_name = "ch_item_master"
app_title = "CH Item Master"
app_publisher = "GoStack"
app_description = "Common Item Master"
app_email = "contact@gostack.in"
app_license = "custom"
required_apps = ["frappe/erpnext"]

add_to_apps_screen = [
	{
		"name": "ch_item_master",
		"logo": "/assets/ch_item_master/images/icon.svg",
		"title": "CH Item Master",
		"route": "/app/ch-item-master",
		"has_permission": "ch_item_master.ch_item_master.utils.check_app_permission",
	}
]

app_include_js = "public/js/item_quick_entry.js"
doctype_js = {"Item": "public/js/item.js"}

# Installation / Migration
after_install = "ch_item_master.install.after_install"
after_migrate = [
	"ch_item_master.setup.setup_roles",
	"ch_item_master.setup.create_ch_custom_fields",
	"ch_item_master.setup.setup_channels",
	"ch_item_master.setup.sync_workspace",
	"ch_item_master.ch_item_master.backfill_ids.backfill_ids_after_migrate",
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
	],
	"hourly": [
		"ch_item_master.ch_item_master.exception_api.expire_stale_exceptions",
	],
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
		"on_submit": "ch_item_master.ch_customer_master.hooks.on_sales_invoice_submit",
	},
	"POS Invoice": {
		"on_submit": "ch_item_master.ch_item_master.doctype.ch_scheme_receivable.ch_scheme_receivable.create_from_pos_invoice",
	},
	"Purchase Receipt": {
		"on_submit": "ch_item_master.ch_item_master.overrides.purchase_receipt.on_submit",
		"on_cancel": "ch_item_master.ch_item_master.overrides.purchase_receipt.on_cancel",
	},
	"Service Request": {
		"on_submit": "ch_item_master.ch_customer_master.hooks.on_service_request_submit",
	},
	"Buyback Request": {
		"on_update": "ch_item_master.ch_customer_master.hooks.on_buyback_request_submit",
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
