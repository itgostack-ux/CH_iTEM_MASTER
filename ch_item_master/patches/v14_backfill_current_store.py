"""
Backfill CH Serial Lifecycle.current_store for all existing rows that have
current_warehouse set but current_store empty.

Previously, serial_no.on_update (and all auto-create helpers) only updated
current_warehouse / current_company when a device moved, leaving current_store
pointing to the original purchase store even after inter-store transfers.

This patch is idempotent — re-running it only touches rows where the resolved
store differs from what is already stored.
"""

import frappe


def execute():
    # Fetch all rows that have a warehouse but no store, or whose store
    # doesn't match the warehouse → CH Store mapping.
    rows = frappe.db.sql(
        """
        SELECT lc.name, lc.current_warehouse, lc.current_store
        FROM `tabCH Serial Lifecycle` lc
        WHERE lc.current_warehouse IS NOT NULL
          AND lc.current_warehouse != ''
        """,
        as_dict=True,
    )

    if not rows:
        return

    # Build warehouse → store map in one query
    store_map = {}
    wh_list = list({r.current_warehouse for r in rows if r.current_warehouse})
    if wh_list:
        placeholders = ", ".join(["%s"] * len(wh_list))
        store_rows = frappe.db.sql(
            f"SELECT warehouse, name FROM `tabCH Store` WHERE warehouse IN ({placeholders})",
            wh_list,
            as_dict=True,
        )
        store_map = {r.warehouse: r.name for r in store_rows}

    updated = 0
    for row in rows:
        expected_store = store_map.get(row.current_warehouse, "")
        if expected_store and row.current_store != expected_store:
            frappe.db.set_value(
                "CH Serial Lifecycle",
                row.name,
                "current_store",
                expected_store,
                update_modified=False,
            )
            updated += 1

    frappe.logger("ch_item_master").info(
        f"v14_backfill_current_store: updated {updated} of {len(rows)} lifecycle rows"
    )
