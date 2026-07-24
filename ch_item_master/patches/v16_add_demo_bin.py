"""v16: Backfill Demo bin warehouse for every existing CH Store.

`STORE_BIN_TYPES` was extended to include the Demo bin (in-store demonstration
units, valued stock). `ensure_store_bins()` only auto-creates new bins when a
store is saved; this patch loops over existing stores to make the new bin
available without requiring each store to be re-saved manually.

Idempotent: skips stores that already have the Demo bin linked.
"""

import frappe

from ch_item_master.config import iter_all_rows


def execute():
    from ch_item_master.ch_core.doctype.ch_store.ch_store import ensure_store_bins

    refreshed = 0
    skipped = 0
    for name in iter_all_rows("CH Store", pluck="name", order_by="name asc", page_size=200):
        store = frappe.get_doc("CH Store", name)
        if not store.warehouse or not store.company:
            skipped += 1
            continue
        ensure_store_bins(store)
        refreshed += 1
    frappe.logger("ch_item_master").info(
        "v16_add_demo_bin refreshed %s stores; skipped %s incomplete stores",
        refreshed,
        skipped,
    )
