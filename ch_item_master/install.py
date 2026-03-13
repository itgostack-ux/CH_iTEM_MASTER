# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

from ch_item_master.setup import (
	create_ch_custom_fields,
	setup_item_variant_settings,
	setup_roles,
)


def after_install():
	"""Called after ch_item_master is installed."""
	setup_roles()
	create_ch_custom_fields()
	setup_item_variant_settings()
	seed_discount_reasons()


def before_uninstall():
	"""Called before ch_item_master is uninstalled."""
	from ch_item_master.setup import delete_ch_custom_fields

	delete_ch_custom_fields()


def seed_discount_reasons():
	"""Create default CH Discount Reason records if they don't already exist."""
	import frappe

	reasons = [
		{"reason_name": "Competitive Pricing", "discount_type": "Percentage", "discount_value": 5},
		{"reason_name": "Bulk Purchase", "discount_type": "Percentage", "discount_value": 3},
		{"reason_name": "Loyalty Customer", "discount_type": "Percentage", "discount_value": 2},
		{"reason_name": "Store Manager Discretion", "discount_type": "Percentage", "discount_value": 5},
		{"reason_name": "Display / Demo Unit", "discount_type": "Percentage", "discount_value": 10},
		{"reason_name": "Damaged Packaging", "discount_type": "Percentage", "discount_value": 15},
		{"reason_name": "Festival / Seasonal", "discount_type": "Percentage", "discount_value": 5},
		{"reason_name": "Employee Discount", "discount_type": "Percentage", "discount_value": 8},
		{
			"reason_name": "Customer Negotiation",
			"discount_type": "Percentage",
			"discount_value": 0,
			"allow_manual_entry": 1,
			"max_manual_percent": 10,
		},
	]
	for r in reasons:
		if not frappe.db.exists("CH Discount Reason", r["reason_name"]):
			doc = frappe.new_doc("CH Discount Reason")
			doc.update(r)
			doc.enabled = 1
			doc.insert(ignore_permissions=True)

	frappe.db.commit()
