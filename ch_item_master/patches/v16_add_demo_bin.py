"""v16: Backfill Demo bin warehouse for every existing CH Store.

`STORE_BIN_TYPES` was extended to include the Demo bin (in-store demonstration
units, valued stock). `ensure_store_bins()` only auto-creates new bins when a
store is saved; this patch loops over existing stores to make the new bin
available without requiring each store to be re-saved manually.

Idempotent: skips stores that already have the Demo bin linked.
"""

import frappe


def execute():
    from ch_item_master.ch_core.doctype.ch_store.ch_store import ensure_store_bins

    stores = frappe.get_all("CH Store", pluck="name")
    created = 0
    for name in stores:
        try:
            store = frappe.get_doc("CH Store", name)
            if not store.warehouse or not store.company:
                continue
            ensure_store_bins(store)
            created += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"v16_add_demo_bin: {name}")
    frappe.db.commit()
    print(f"v16_add_demo_bin: refreshed bins for {created}/{len(stores)} stores")
