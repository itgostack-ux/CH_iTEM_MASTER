# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
v20_drop_item_price_ch_mrp — remove ch_mrp from Item Price.

Background:
  ch_mrp was a custom Currency field added to ERPNext's native Item Price
  doctype to display the MRP alongside the selling price. It was populated
  by CH Item Price._sync_to_erp_item_price().

  With the introduction of Item.ch_item_mrp (canonical per-item MRP, v19)
  and its display as a dedicated column in the CH Ready Reckoner, the
  per-channel ch_mrp on Item Price is redundant and has been removed.

Actions:
  1. Delete the Custom Field record (tabCustom Field, dt=Item Price, fieldname=ch_mrp).
  2. Drop the column from tabItem Price if it still exists.

Idempotent: safe to run if the field was already removed.
"""

import frappe


def execute():
	# 1. Remove the Custom Field doc
	cf_name = frappe.db.get_value(
		"Custom Field",
		{"dt": "Item Price", "fieldname": "ch_mrp"},
		"name",
	)
	if cf_name:
		frappe.delete_doc("Custom Field", cf_name, ignore_permissions=True, force=True)
		frappe.db.commit()
		print(f"[v20] Deleted Custom Field {cf_name} (Item Price.ch_mrp)")
	else:
		print("[v20] Custom Field Item Price.ch_mrp not found — already removed or never existed.")

	# 2. Drop the actual DB column if it still exists
	col_exists = frappe.db.sql(
		"SHOW COLUMNS FROM `tabItem Price` LIKE 'ch_mrp'"
	)
	if col_exists:
		frappe.db.sql("ALTER TABLE `tabItem Price` DROP COLUMN `ch_mrp`")
		frappe.db.commit()
		print("[v20] Dropped column tabItem Price.ch_mrp")
	else:
		print("[v20] Column tabItem Price.ch_mrp not present — nothing to drop.")
