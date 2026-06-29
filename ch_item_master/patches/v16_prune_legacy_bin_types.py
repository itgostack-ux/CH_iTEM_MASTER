"""v16 Path B Phase 1: disable empty legacy per-store bin warehouses.

`STORE_BIN_TYPES` was narrowed from 6 bins to 3 (Damaged, Demo, Buyback).
The 3 dropped bin types (In-Transit, Disposed, Reserved) created per-store
sibling warehouses for every CH Store. In the QA dataset almost all of
those warehouses are empty (no stock_value, no Stock Ledger Entries) yet
they bloat the warehouse tree and confuse operators (1 store -> 7 nodes).

This patch:
  * Finds every Warehouse with ch_bin_type in (In-Transit, Disposed, Reserved)
  * If stock_value = 0 in tabBin AND no Stock Ledger Entries reference it
    -> sets disabled = 1 (recoverable, NOT deleted)
  * Otherwise logs a warning row so operators can review manually.

Idempotent: skips warehouses that are already disabled or have stock/SLE.
Reversible: re-enable a warehouse from the Warehouse form to restore.

Sellable warehouses (the base store warehouse itself) are NEVER touched.
Buyback / Damaged / Demo (the 3 retained bins) are NEVER touched.
"""

import frappe


LEGACY_TYPES = ("In-Transit", "Disposed", "Reserved")


def execute():
	warehouses = frappe.get_all(
		"Warehouse",
		filters={"ch_bin_type": ("in", LEGACY_TYPES), "disabled": 0},
		fields=["name", "ch_bin_type", "company"],
	)

	disabled = 0
	skipped_with_stock = []

	for w in warehouses:
		stock_value = (
			frappe.db.sql(
				"SELECT COALESCE(SUM(stock_value), 0) FROM `tabBin` WHERE warehouse = %s",
				w.name,
			)[0][0]
			or 0
		)
		sle_count = frappe.db.count("Stock Ledger Entry", {"warehouse": w.name})

		if stock_value or sle_count:
			skipped_with_stock.append(
				f"{w.name} (type={w.ch_bin_type}, stock_value={stock_value}, sle={sle_count})"
			)
			continue

		frappe.db.set_value("Warehouse", w.name, "disabled", 1, update_modified=False)
		disabled += 1

	frappe.db.commit()

	print(
		f"v16_prune_legacy_bin_types: disabled {disabled} empty legacy bin warehouses "
		f"(In-Transit / Disposed / Reserved); skipped {len(skipped_with_stock)} with stock or SLE history."
	)
	if skipped_with_stock:
		print("v16_prune_legacy_bin_types: review the following manually:")
		for line in skipped_with_stock:
			print(f"  - {line}")
