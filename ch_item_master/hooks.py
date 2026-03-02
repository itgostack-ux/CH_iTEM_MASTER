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

doctype_js = {"Item": "public/js/item.js"}

# Installation / Migration
after_install = "ch_item_master.install.after_install"
after_migrate = [
	"ch_item_master.setup.setup_roles",
	"ch_item_master.setup.create_ch_custom_fields",
	"ch_item_master.setup.setup_channels",
	"ch_item_master.setup.sync_workspace",
]
before_uninstall = "ch_item_master.install.before_uninstall"

# Scheduled Tasks
scheduler_events = {
	"daily_long": [
		"ch_item_master.ch_item_master.scheduled_tasks.auto_expire_records",
		"ch_item_master.ch_customer_master.doctype.ch_loyalty_transaction.ch_loyalty_transaction.expire_loyalty_points",
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
	"Customer": {
		"before_insert": "ch_item_master.ch_customer_master.overrides.customer.before_insert",
		"validate": "ch_item_master.ch_customer_master.overrides.customer.validate",
	},
	"Sales Invoice": {
		"on_submit": "ch_item_master.ch_customer_master.hooks.on_sales_invoice_submit",
	},
	"Service Request": {
		"on_submit": "ch_item_master.ch_customer_master.hooks.on_service_request_submit",
	},
	"Buyback Request": {
		"on_submit": "ch_item_master.ch_customer_master.hooks.on_buyback_request_submit",
	},
	# Transactions: manual rate-filling removed.
	# ERPNext natively uses Item Price (fetched by price list) and applies Pricing Rules.
	# CH Item Price.on_update() syncs into ERPNext Item Price on save.
	# CH Item Offer.approve() syncs into ERPNext Pricing Rule on approval.
	# No manual hook required for Sales Order, Sales Invoice, Quotation, etc.
}
