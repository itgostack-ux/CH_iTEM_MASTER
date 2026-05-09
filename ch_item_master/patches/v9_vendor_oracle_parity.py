# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Patch v9: Vendor sourcing Oracle-parity foundations.

1. Add indexes for company/org/rank filtering on CH Vendor Info Record
2. Backfill approval_status on existing records
3. Ensure allocation defaults to 100 when empty
"""

import frappe


def execute():
	_add_vendor_indexes()
	_backfill_vendor_governance_fields()
	frappe.db.commit()


def _add_vendor_indexes():
	if not frappe.db.table_exists("CH Vendor Info Record"):
		return

	index_sql = [
		"ALTER TABLE `tabCH Vendor Info Record` ADD INDEX IF NOT EXISTS `idx_company_org_rank` (`company`, `purchase_org`, `source_rank`)",
		"ALTER TABLE `tabCH Vendor Info Record` ADD INDEX IF NOT EXISTS `idx_item_supplier_scope` (`item_code`, `supplier`, `company`, `purchase_org`)",
		"ALTER TABLE `tabCH Vendor Info Record` ADD INDEX IF NOT EXISTS `idx_approval_active` (`approval_status`, `active`)",
	]
	for stmt in index_sql:
		try:
			frappe.db.sql(stmt)
		except Exception:
			frappe.log_error(title="v9 vendor index creation failed", message=frappe.get_traceback())


def _backfill_vendor_governance_fields():
	if not frappe.db.table_exists("CH Vendor Info Record"):
		return

	if frappe.db.has_column("CH Vendor Info Record", "approval_status"):
		frappe.db.sql(
			"""
			UPDATE `tabCH Vendor Info Record`
			SET `approval_status` = 'Approved'
			WHERE COALESCE(`approval_status`, '') = ''
			"""
		)

	if frappe.db.has_column("CH Vendor Info Record", "allocation_pct"):
		frappe.db.sql(
			"""
			UPDATE `tabCH Vendor Info Record`
			SET `allocation_pct` = 100
			WHERE `allocation_pct` IS NULL
			"""
		)

	if frappe.db.has_column("CH Vendor Info Record", "source_rank"):
		frappe.db.sql(
			"""
			UPDATE `tabCH Vendor Info Record`
			SET `source_rank` = 1
			WHERE `source_rank` IS NULL OR `source_rank` <= 0
			"""
		)
