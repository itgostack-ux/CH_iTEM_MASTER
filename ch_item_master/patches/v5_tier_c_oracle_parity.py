# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Patch v5 — Tier C Oracle-parity: version control, approval gate, GTIN,
MRP/coverage planning, vendor info-record, full PLM state machine.

Safe to re-run (idempotent checks everywhere).
"""

import frappe


def execute():
	_add_tier_c_item_indexes()
	_update_audit_log_actions()
	_backfill_plm_status()
	_backfill_approval_status()
	frappe.db.commit()


# ─── Indexes ─────────────────────────────────────────────────────────────────

def _add_tier_c_item_indexes():
	"""Add indexes for new Tier C fields on tabItem and tabCH Item Version.

	NOTE: frappe.db.table_exists() takes the DocType name WITHOUT the 'tab' prefix.
	Frappe adds the prefix internally. Using "tabItem" would check for "tabtabItem" → always False.
	"""
	index_specs = [
		("tabItem", "Item", "ch_plm_status"),
		("tabItem", "Item", "ch_approval_status"),
		("tabItem", "Item", "ch_gtin"),
		("tabCH Item Version", "CH Item Version", "item_code"),
		("tabCH Item Version", "CH Item Version", "version_number"),
		("tabCH Vendor Info Record", "CH Vendor Info Record", "item_code"),
		("tabCH Vendor Info Record", "CH Vendor Info Record", "supplier"),
	]
	for table, doctype, col in index_specs:
		try:
			# Use doctype name (without tab) — Frappe adds the prefix internally
			if not frappe.db.table_exists(doctype):
				continue
			if not frappe.db.has_column(doctype, col):
				continue
			existing = frappe.db.sql(
				f"SHOW INDEX FROM `{table}` WHERE Column_name = %s AND Key_name = %s",
				(col, f"idx_{col}"),
			)
			if not existing:
				frappe.db.sql(f"ALTER TABLE `{table}` ADD INDEX `idx_{col}` (`{col}`)")
		except Exception:
			frappe.log_error(
				title=f"v5 index creation failed: {table}.{col}",
				message=frappe.get_traceback(),
			)


# ─── Audit Log Action Options ─────────────────────────────────────────────────

def _update_audit_log_actions():
	"""Ensure CH Item Audit Log action field includes Tier C actions."""
	new_actions = [
		"PLM Status Changed",
		"Approval Submitted",
		"Approval Approved",
		"Approval Rejected",
		"Version Snapshot",
	]
	al_meta = frappe.get_meta("CH Item Audit Log")
	action_field = next((f for f in al_meta.fields if f.fieldname == "action"), None)
	if not action_field:
		return
	current = action_field.options or ""
	current_set = set(o.strip() for o in current.splitlines())
	added = False
	for action in new_actions:
		if action not in current_set:
			current += f"\n{action}"
			added = True
	if added:
		frappe.db.set_value(
			"Custom Field",
			{"dt": "CH Item Audit Log", "fieldname": "action"},
			"options",
			current.strip(),
			update_modified=False,
		)


# ─── Backfills ───────────────────────────────────────────────────────────────

def _backfill_plm_status():
	"""Set ch_plm_status = 'NPI' for all items that have no value yet."""
	if not frappe.db.table_exists("tabItem") or not frappe.db.has_column("Item", "ch_plm_status"):
		return
	frappe.db.sql(
		"UPDATE `tabItem` SET `ch_plm_status` = 'NPI' WHERE COALESCE(`ch_plm_status`, '') = ''"
	)


def _backfill_approval_status():
	"""
	Backfill ch_approval_status for existing items:
	  - Active lifecycle → Approved
	  - All others       → Draft
	"""
	if not frappe.db.table_exists("tabItem") or not frappe.db.has_column("Item", "ch_approval_status"):
		return
	frappe.db.sql(
		"UPDATE `tabItem` SET `ch_approval_status` = 'Approved' "
		"WHERE COALESCE(`ch_approval_status`, '') = '' AND `ch_lifecycle_status` = 'Active'"
	)
	frappe.db.sql(
		"UPDATE `tabItem` SET `ch_approval_status` = 'Draft' "
		"WHERE COALESCE(`ch_approval_status`, '') = '' AND `ch_lifecycle_status` != 'Active'"
	)
