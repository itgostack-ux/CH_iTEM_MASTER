# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Patch: Backfill ch_lifecycle_status='Active' for existing items, default
sub-categories and categories to Active, and add performance indexes used
by item_nature link queries and lifecycle filtering.

Idempotent — safe to re-run.
"""

import frappe


def execute() -> None:
	_upgrade_lifecycle_custom_field()
	_relax_ch_model_reqd()
	_backfill_item_lifecycle()
	_backfill_master_lifecycle("CH Sub Category")
	_backfill_master_lifecycle("CH Category")
	_add_indexes()


def _upgrade_lifecycle_custom_field() -> None:
	"""Force-update the legacy ch_lifecycle_status Custom Field options
	(Active/End of Life/Discontinued) → new Tier A state machine
	(Draft/Pending Review/Active/Obsolete/Blocked). Idempotent."""
	name = "Item-ch_lifecycle_status"
	if not frappe.db.exists("Custom Field", name):
		return
	new_options = "Draft\nPending Review\nActive\nObsolete\nBlocked"
	frappe.db.set_value(
		"Custom Field",
		name,
		{
			"options": new_options,
			"default": "Draft",
			"in_list_view": 1,
			"in_standard_filter": 1,
			"label": "Lifecycle Status",
			"description": "Governance lifecycle (Tier A). Items must be Active to be used in transactions.",
		},
		update_modified=False,
	)
	frappe.clear_cache(doctype="Item")


def _relax_ch_model_reqd() -> None:
	"""ch_model is mandatory only for Variant Template sub-categories;
	enforced server-side. Drop the global reqd=1 flag if still set."""
	name = "Item-ch_model"
	if frappe.db.exists("Custom Field", name):
		frappe.db.set_value("Custom Field", name, "reqd", 0, update_modified=False)
		frappe.clear_cache(doctype="Item")


def _backfill_item_lifecycle() -> None:
	if not frappe.db.has_column("Item", "ch_lifecycle_status"):
		return
	# Map legacy values → new state machine.
	frappe.db.sql("""UPDATE `tabItem` SET ch_lifecycle_status='Obsolete' WHERE ch_lifecycle_status='End of Life'""")
	frappe.db.sql("""UPDATE `tabItem` SET ch_lifecycle_status='Blocked' WHERE ch_lifecycle_status='Discontinued'""")
	# Existing items with empty lifecycle are presumed live → mark Active.
	frappe.db.sql(
		"""UPDATE `tabItem`
			SET ch_lifecycle_status = 'Active'
			WHERE IFNULL(ch_lifecycle_status, '') = ''"""
	)
	frappe.db.commit()


def _backfill_master_lifecycle(doctype: str) -> None:
	if not frappe.db.has_column(doctype, "lifecycle_status"):
		return
	tbl = f"tab{doctype}"
	frappe.db.sql(
		f"""UPDATE `{tbl}`
			SET lifecycle_status = 'Active'
			WHERE IFNULL(lifecycle_status, '') = ''"""
	)
	frappe.db.commit()


_INDEXES = [
	("Item", ["ch_lifecycle_status"], "idx_ch_lifecycle"),
	("Item", ["ch_sub_category"], "idx_ch_sub_category"),
	("Item", ["ch_category"], "idx_ch_category"),
	("CH Sub Category", ["item_nature"], "idx_item_nature"),
	("CH Sub Category", ["lifecycle_status"], "idx_sc_lifecycle"),
	("CH Category", ["lifecycle_status"], "idx_cat_lifecycle"),
]


def _add_indexes() -> None:
	for doctype, cols, idx_name in _INDEXES:
		try:
			# add_index is idempotent (no-op when index exists)
			frappe.db.add_index(doctype, cols, index_name=idx_name)
		except Exception:
			frappe.log_error(
				title=f"Index creation failed: {doctype}.{idx_name}",
				message=frappe.get_traceback(),
			)
	frappe.db.commit()
