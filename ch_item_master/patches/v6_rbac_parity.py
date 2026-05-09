# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Patch v6: RBAC Oracle/SAP-parity implementation.

1. Set permlevel=1 on sensitive Item custom fields
2. Install Custom DocPerm records at permlevel=1 for pricing/compliance roles
3. Ensure all 6 new granular roles exist
4. Add DB indexes for delegation / role-assignment queries
"""

import frappe


def execute():
	_ensure_new_roles()
	_set_sensitive_field_permlevel()
	_install_custom_docperms()
	_add_rbac_indexes()


# ─────────────────────────────────────────────────────────────────────────────

_NEW_ROLES = [
	{"role_name": "CH Item Creator",  "desk_access": 1, "is_custom": 1},
	{"role_name": "CH Item Reviewer", "desk_access": 1, "is_custom": 1},
	{"role_name": "CH PLM Manager",   "desk_access": 1, "is_custom": 1},
	{"role_name": "CH Vendor Manager","desk_access": 1, "is_custom": 1},
	{"role_name": "CH MRP Planner",   "desk_access": 1, "is_custom": 1},
	{"role_name": "CH GTIN Editor",   "desk_access": 1, "is_custom": 1},
]

_SENSITIVE_FIELDS = [
	"Item-ch_standard_cost",
	"Item-ch_standard_cost_updated_on",
	"Item-ch_minimum_selling_price",
	"Item-ch_msp_effective_from",
	"Item-ch_gtin",
]


def _ensure_new_roles():
	"""Create the 6 new granular roles if not already present."""
	for role_def in _NEW_ROLES:
		if not frappe.db.exists("Role", role_def["role_name"]):
			doc = frappe.new_doc("Role")
			doc.update(role_def)
			doc.insert(ignore_permissions=True)


def _set_sensitive_field_permlevel():
	"""
	Set permlevel=1 on the 5 sensitive Item custom fields.
	create_custom_fields(update=False) does NOT update existing fields, so we
	must use db.set_value to backfill this on existing installations.
	"""
	for cf_name in _SENSITIVE_FIELDS:
		if frappe.db.exists("Custom Field", cf_name):
			current = frappe.db.get_value("Custom Field", cf_name, "permlevel") or 0
			if int(current) != 1:
				frappe.db.set_value("Custom Field", cf_name, "permlevel", 1, update_modified=False)


def _install_custom_docperms():
	"""Delegate to rbac.install_custom_docperms (idempotent)."""
	try:
		from ch_item_master.ch_item_master.rbac import install_custom_docperms
		result = install_custom_docperms()
		frappe.db.commit()
		frappe.logger().info(f"v6 patch: {result}")
	except Exception:
		frappe.log_error(title="v6 patch: install_custom_docperms failed", message=frappe.get_traceback())


def _add_rbac_indexes():
	"""Add performance indexes for RBAC queries."""
	# ch_submitted_by on Item (SoD queries)
	if frappe.db.table_exists("Item"):
		if frappe.db.has_column("Item", "ch_submitted_by"):
			try:
				frappe.db.sql(
					"ALTER TABLE `tabItem` ADD INDEX IF NOT EXISTS `idx_ch_submitted_by` (`ch_submitted_by`)"
				)
			except Exception:
				pass  # index may already exist

	# CH Approval Delegation: delegate + active
	if frappe.db.table_exists("CH Approval Delegation"):
		try:
			frappe.db.sql(
				"ALTER TABLE `tabCH Approval Delegation` "
				"ADD INDEX IF NOT EXISTS `idx_delegate_active` (`delegate`, `active`, `valid_to`)"
			)
		except Exception:
			pass

	# CH Role Assignment: user + status + valid_to
	if frappe.db.table_exists("CH Role Assignment"):
		try:
			frappe.db.sql(
				"ALTER TABLE `tabCH Role Assignment` "
				"ADD INDEX IF NOT EXISTS `idx_status_valid_to` (`status`, `valid_to`)"
			)
		except Exception:
			pass
