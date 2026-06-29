"""v17 Path B Phase 2: warehouse geographic-hierarchy migration.

Migrates existing CH Store data from the legacy "flat siblings" warehouse
layout to the SAP/Oracle-aligned 4-level tree:

	All Warehouses - <ABBR>
	└── <City> - <ABBR>             (group, City Group)
	    └── <Zone> - <ABBR>         (group, Zone Group)
	        ├── <Zone> Hub - <ABBR> (leaf, existing zone hub)
	        └── <Store> - <ABBR>    (group, Store Group)
	            ├── <Store>-Sellable - <ABBR>  (leaf, RENAMED from old base)
	            ├── <Store>-Damaged  - <ABBR>  (leaf, reparented)
	            ├── <Store>-Demo     - <ABBR>  (leaf, reparented)
	            └── <Store>-Buyback  - <ABBR>  (leaf, reparented)

Renames use ``frappe.rename_doc`` which cascades to every Link/Dynamic Link
field, child table, and recorded reference (Bin, Stock Ledger Entry,
Serial No, Stock Entry items, Sales Order items, POS Profile, etc.). No
SLE replay, no stock movement, audit trail preserved verbatim.

CH Store.warehouse is intentionally LEFT pointing at the Sellable LEAF
(post-rename) — ERPNext blocks transactions on group warehouses, so the
field must keep its leaf semantics. A new ``warehouse_group`` field is
populated with the Store Group name for hierarchy/display use only.

Idempotent. Safe to re-run.
"""

import frappe

from ch_item_master.ch_core.warehouse_geo import restructure_all_stores


def execute():
	# warehouse_group is brand new — give the schema a moment if migrate is
	# still applying the doctype change in the same run.
	if not frappe.db.has_column("CH Store", "warehouse_group"):
		frappe.reload_doc("ch_item_master", "doctype", "ch_store")

	# Custom fields are normally re-synced by after_migrate, but this patch
	# runs BEFORE after_migrate — so the ch_location_type Select still has
	# the old options and rejects 'City Group' / 'Zone Group' / 'Store Group'.
	# Update the Custom Field options in-place so our inserts pass validation.
	_ensure_location_type_options()

	stats = restructure_all_stores()
	print(
		"v17_warehouse_geographic_hierarchy: "
		f"total={stats['total']} migrated={stats['migrated']} "
		f"skipped={stats['skipped']} errors={stats['errors']}"
	)

	# Per-store summary for the operator log
	for d in stats.get("details", []):
		if d.get("skipped"):
			print(f"  skip  {d['store']}: {d['skipped']}")
		else:
			rename = ""
			if d.get("renamed_sellable_to"):
				rename = f" renamed -> {d['renamed_sellable_to']}"
			print(
				f"  ok    {d['store']}: group={d['store_group']} "
				f"reparented={len(d['reparented'])}{rename}"
			)


def _ensure_location_type_options():
	"""Refresh the ch_location_type Custom Field so it accepts the new groups."""
	required_opts = "\nCity Group\nZone Group\nStore Group\nStore Warehouse\nZone Warehouse\nTransit Warehouse\nService Warehouse\nStore Bin\nOther"
	cf = frappe.db.get_value(
		"Custom Field",
		{"dt": "Warehouse", "fieldname": "ch_location_type"},
		["name", "options"],
		as_dict=True,
	)
	if not cf:
		return
	if cf.options != required_opts:
		frappe.db.set_value(
			"Custom Field", cf.name, "options", required_opts, update_modified=False
		)
		frappe.clear_cache(doctype="Warehouse")
