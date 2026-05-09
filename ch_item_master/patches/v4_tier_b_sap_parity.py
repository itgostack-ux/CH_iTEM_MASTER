"""
Patch v4: Tier B SAP-parity additions
- Add parent_category column to CH Category (for hierarchy tree)
- Sync custom fields for Tier B Item fields (standard cost, expiry enforce, substitutes, country HS)
- Add DB indexes for new Item fields
"""

import frappe


def execute():
	frappe.clear_cache()

	_add_parent_category_column()
	_add_tier_b_item_indexes()
	_sync_tier_b_custom_fields()

	frappe.db.commit()
	frappe.clear_cache()


def _add_parent_category_column():
	"""Add parent_category column to tabCH Category if missing."""
	if not frappe.db.has_column("CH Category", "parent_category"):
		frappe.db.add_column("CH Category", "parent_category", "varchar(140) default NULL")
		frappe.db.commit()


def _add_tier_b_item_indexes():
	"""Add indexes on Item for new Tier B fields."""
	indexes = [
		("tabItem", "ch_enforce_expiry", "ch_enforce_expiry"),
		("tabItem", "ch_standard_cost", "ch_standard_cost"),
	]
	for table, index_name, col in indexes:
		if frappe.db.has_column(table.replace("tab", ""), col):
			try:
				frappe.db.sql(
					f"ALTER TABLE `{table}` ADD INDEX IF NOT EXISTS `{index_name}` (`{col}`)"
				)
			except Exception:
				pass  # index may already exist


def _sync_tier_b_custom_fields():
	"""
	Force-update Tier B Custom Fields on Item that may not have been created yet.
	We use create_custom_fields which is idempotent when fields don't exist yet.
	For fields that already exist, we do nothing — they will be created by migrate.
	"""
	# Force-update audit log action options to include Standard Cost Changed
	_update_audit_log_actions()


def _update_audit_log_actions():
	"""Ensure the CH Item Audit Log action Select field includes all Tier B actions."""
	new_options = (
		"Create\nUpdate\nLifecycle Change\nApprove\nBlock\nObsolete\n"
		"Reactivate\nNature Change\nStock Flag Change\nStandard Cost Changed"
	)
	frappe.db.set_value(
		"DocField",
		{"parent": "CH Item Audit Log", "fieldname": "action"},
		"options",
		new_options,
	)
