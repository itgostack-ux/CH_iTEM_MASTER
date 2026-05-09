# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Patch v7 — Fix missing Tier C indexes.

v5 patch had a bug: frappe.db.table_exists("tabItem") always returns False because
Frappe adds the "tab" prefix internally (making it check for "tabtabItem").
This patch creates the missing indexes directly via SQL (idempotent using IF NOT EXISTS).
"""

import frappe


def execute():
	_create_missing_indexes()
	frappe.db.commit()


_INDEX_SPECS = [
	# (table, column, index_name)
	("tabItem", "ch_plm_status", "idx_ch_plm_status"),
	("tabItem", "ch_approval_status", "idx_ch_approval_status"),
	("tabItem", "ch_gtin", "idx_ch_gtin"),
	("tabCH Item Version", "item_code", "idx_item_code"),
	("tabCH Item Version", "version_number", "idx_version_number"),
	("tabCH Vendor Info Record", "item_code", "idx_item_code"),
	("tabCH Vendor Info Record", "supplier", "idx_supplier"),
]


def _create_missing_indexes():
	for table, column, idx_name in _INDEX_SPECS:
		try:
			# Check if column exists before adding index
			cols = frappe.db.sql(
				f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,)
			)
			if not cols:
				continue  # column not yet migrated, skip

			# Check if index already exists
			existing = frappe.db.sql(
				f"SHOW INDEX FROM `{table}` WHERE Column_name = %s AND Key_name = %s",
				(column, idx_name),
			)
			if existing:
				continue  # already exists

			frappe.db.sql(
				f"ALTER TABLE `{table}` ADD INDEX `{idx_name}` (`{column}`)"
			)
			frappe.logger().info(f"v7 patch: created index {idx_name} on {table}.{column}")
		except Exception:
			frappe.log_error(
				title=f"v7 index creation failed: {table}.{column}",
				message=frappe.get_traceback(),
			)
