"""v12 — Collapse the per-store Sellable bin into the store's base warehouse.

Architecture change (see the location-hierarchy discussion):

    BEFORE                                AFTER
    {store warehouse} (group)             {store warehouse} (leaf, ch_bin_type='Sellable')
      ├ {store}-Sellable    (leaf)        {store}-InTransit  (sibling leaf)
      ├ {store}-InTransit   (leaf)        {store}-Damaged    (sibling leaf)
      ├ {store}-Damaged     (leaf)        {store}-Disposed   (sibling leaf)
      ├ {store}-Disposed    (leaf)        {store}-Reserved   (sibling leaf)
      ├ {store}-Reserved    (leaf)        {store}-Buyback    (sibling leaf)
      └ {store}-Buyback     (leaf)

For each enabled CH Store:
  1. Reparent every child bin to the store warehouse's parent (so they become
     siblings of the store warehouse instead of children).
  2. Demote the store warehouse to a leaf (is_group=0) so it can post SLEs.
  3. Stamp the store warehouse with ch_bin_type='Sellable' and ch_location_type
     'Store Warehouse'.
  4. Move stock from {store}-Sellable into the store warehouse via a Material
     Transfer Stock Entry (only if the legacy Sellable bin has positive qty).
  5. Disable the legacy Sellable bin (kept on disk for SLE history).

The patch is idempotent.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt


def execute():
	if not frappe.db.table_exists("CH Store"):
		return
	if not frappe.db.has_column("Warehouse", "ch_store"):
		return

	stores = frappe.get_all(
		"CH Store",
		filters={"disabled": 0},
		fields=["name", "warehouse", "company"],
	)

	for st in stores:
		try:
			_collapse_one(st)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				frappe.get_traceback(),
				f"v12_collapse_sellable_bin: failed for store {st.name}",
			)


def _collapse_one(st):
	base_name = st.warehouse
	if not base_name:
		return

	base = frappe.db.get_value(
		"Warehouse",
		base_name,
		["name", "is_group", "parent_warehouse", "ch_bin_type"],
		as_dict=True,
	)
	if not base:
		return

	# --- Step 1: reparent children to the grandparent --------------------
	grandparent = base.parent_warehouse or None
	children = frappe.get_all(
		"Warehouse",
		filters={"parent_warehouse": base.name},
		fields=["name", "ch_bin_type"],
	)
	for ch in children:
		# Skip self-references (shouldn't happen but be safe)
		if ch.name == base.name:
			continue
		frappe.db.set_value(
			"Warehouse", ch.name, "parent_warehouse", grandparent,
			update_modified=False,
		)
	if children:
		frappe.db.commit()

	# --- Step 2: demote base to a leaf so it can post SLEs ---------------
	if base.is_group:
		frappe.db.set_value(
			"Warehouse", base.name, "is_group", 0, update_modified=False,
		)
		frappe.db.commit()

	# --- Step 3: stamp base as the Sellable warehouse --------------------
	updates = {}
	if (base.ch_bin_type or "") != "Sellable":
		updates["ch_bin_type"] = "Sellable"
	updates["ch_location_type"] = "Store Warehouse"
	updates["ch_store"] = st.name
	frappe.db.set_value("Warehouse", base.name, updates, update_modified=False)
	frappe.db.commit()

	# --- Step 4: move stock from legacy Sellable bin → base warehouse ----
	legacy_sellable = frappe.db.get_value(
		"Warehouse",
		{
			"ch_store": st.name,
			"ch_bin_type": "Sellable",
			"name": ("!=", base.name),
			"disabled": 0,
		},
		"name",
	)

	if legacy_sellable:
		bins_with_stock = frappe.get_all(
			"Bin",
			filters={"warehouse": legacy_sellable, "actual_qty": (">", 0)},
			fields=["item_code", "actual_qty"],
		)

		if bins_with_stock:
			se = frappe.new_doc("Stock Entry")
			se.stock_entry_type = "Material Transfer"
			se.purpose = "Material Transfer"
			se.company = st.company
			se.from_warehouse = legacy_sellable
			se.to_warehouse = base.name

			has_cf = frappe.db.exists(
				"Custom Field",
				{"dt": "Stock Entry", "fieldname": "ch_bin_transfer_reason"},
			)
			if has_cf:
				se.ch_from_bin_type = "Sellable"
				se.ch_to_bin_type = "Sellable"
				se.ch_store = st.name

			for b in bins_with_stock:
				row = se.append("items", {})
				row.item_code = b.item_code
				row.qty = flt(b.actual_qty)
				row.s_warehouse = legacy_sellable
				row.t_warehouse = base.name
				serials = frappe.get_all(
					"Serial No",
					filters={
						"item_code": b.item_code,
						"warehouse": legacy_sellable,
						"status": "Active",
					},
					pluck="name",
				)
				if serials:
					row.serial_no = "\n".join(serials)

			se.flags.ignore_permissions = True
			se.insert()
			se.submit()
			frappe.db.commit()

		# --- Step 5: disable the legacy Sellable bin ---------------------
		frappe.db.set_value(
			"Warehouse", legacy_sellable, "disabled", 1, update_modified=False,
		)
		frappe.db.commit()
