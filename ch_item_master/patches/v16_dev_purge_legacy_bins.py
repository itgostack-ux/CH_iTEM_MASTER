"""
DEV-ONLY destructive cleanup: wipe legacy bin warehouses and the
just-renamed Sellable LEGACY zombies, including all dependent rows.

Removes:
  * Warehouses with ch_bin_type IN (In-Transit, Disposed, Reserved)
  * Warehouses matching ``%-Sellable-LEGACY - BMPL``
  * Stock Entry vouchers that reference any of the above (parent + child)
  * Stock Ledger Entry rows for those vouchers AND for any doomed warehouse
  * tabBin rows on the doomed warehouses
  * CH Bin Transfer Reason rows whose from_bin_type / to_bin_type is legacy
  * Pointers from CH Store and CH Store Zone

After deleting, recomputes tabBin.actual_qty for surviving (item, warehouse)
pairs that lost SLE rows so on-hand stock reflects what remains in the
ledger.

Run via:
    bench --site erpnext.local execute \
        ch_item_master.patches.v16_dev_purge_legacy_bins.execute
"""

from __future__ import annotations

from collections import defaultdict

import frappe


LEGACY_BIN_TYPES = ("In-Transit", "Disposed", "Reserved")
LEGACY_NAME_PATTERN = "%-Sellable-LEGACY - BMPL"


def execute():
    site = frappe.local.site
    print(f"[purge] Running on site: {site}")
    print(f"[purge] Removing bin types: {LEGACY_BIN_TYPES}")

    # 1. Inventory the warehouses to wipe.
    warehouses = frappe.db.sql_list(
        """
        SELECT name FROM tabWarehouse
        WHERE ch_bin_type IN %(types)s
           OR name LIKE %(pat)s
        """,
        {"types": LEGACY_BIN_TYPES, "pat": LEGACY_NAME_PATTERN},
    )
    print(f"[purge] Warehouses targeted: {len(warehouses)}")
    for w in warehouses:
        print(f"          - {w}")
    if not warehouses:
        print("[purge] Nothing to do.")
        return

    wh_tuple = tuple(warehouses)

    # 2. Find all SLE vouchers that touch any doomed warehouse.
    voucher_rows = frappe.db.sql(
        """
        SELECT DISTINCT voucher_type, voucher_no
        FROM `tabStock Ledger Entry`
        WHERE warehouse IN %(w)s
        """,
        {"w": wh_tuple},
        as_dict=True,
    )
    vouchers_by_type = defaultdict(set)
    for r in voucher_rows:
        vouchers_by_type[r.voucher_type].add(r.voucher_no)
    total_vouchers = sum(len(v) for v in vouchers_by_type.values())
    print(f"[purge] Vouchers referencing those warehouses: {total_vouchers}")
    for vt, names in vouchers_by_type.items():
        print(f"          {vt}: {len(names)}")

    # 3. Compute (item, surviving_warehouse) pairs we will need to repost.
    affected = set()  # (item_code, warehouse)
    for vt, names in vouchers_by_type.items():
        rows = frappe.db.sql(
            """
            SELECT DISTINCT item_code, warehouse
            FROM `tabStock Ledger Entry`
            WHERE voucher_type=%s AND voucher_no IN %s
            """,
            (vt, tuple(names)),
            as_dict=True,
        )
        for r in rows:
            if r.warehouse not in warehouses:
                affected.add((r.item_code, r.warehouse))
    print(f"[purge] Surviving (item, warehouse) pairs to repost: {len(affected)}")

    # 4. Hard-delete vouchers (parent + child tables).
    #    We only handle Stock Entry here because the inventory query showed
    #    nothing else; add more voucher types here if a future run finds them.
    for vt, names in vouchers_by_type.items():
        names_tuple = tuple(names)
        if vt == "Stock Entry":
            frappe.db.sql(
                "DELETE FROM `tabStock Entry Detail` WHERE parent IN %s",
                (names_tuple,),
            )
            frappe.db.sql(
                "DELETE FROM `tabStock Entry` WHERE name IN %s",
                (names_tuple,),
            )
            print(f"[purge] Deleted {len(names)} Stock Entry vouchers")
        else:
            print(
                f"[purge] WARNING: voucher type {vt!r} not handled, "
                f"its SLE rows will be removed but the parent doc will remain."
            )

    # 5. Delete all SLE rows for those vouchers (both sides of every transfer).
    for vt, names in vouchers_by_type.items():
        frappe.db.sql(
            """
            DELETE FROM `tabStock Ledger Entry`
            WHERE voucher_type=%s AND voucher_no IN %s
            """,
            (vt, tuple(names)),
        )

    # 6. Delete any remaining SLE rows on the doomed warehouses
    #    (e.g. orphan rows whose voucher we somehow missed).
    sle_count = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE warehouse IN %s",
        (wh_tuple,),
    )[0][0]
    if sle_count:
        frappe.db.sql(
            "DELETE FROM `tabStock Ledger Entry` WHERE warehouse IN %s",
            (wh_tuple,),
        )
        print(f"[purge] Deleted {sle_count} orphan SLE rows")

    # 7. Delete tabBin rows on the doomed warehouses.
    bin_count = frappe.db.sql(
        "SELECT COUNT(*) FROM tabBin WHERE warehouse IN %s", (wh_tuple,)
    )[0][0]
    frappe.db.sql("DELETE FROM tabBin WHERE warehouse IN %s", (wh_tuple,))
    print(f"[purge] Deleted {bin_count} tabBin rows")

    # 8. CH Bin Transfer Reason rows that reference legacy bin types.
    #    The doctype columns are ``source_bin_type`` / ``target_bin_type``
    #    (NOT from_/to_) — verified via SHOW COLUMNS.
    bt_tuple = tuple(LEGACY_BIN_TYPES)
    reason_count = frappe.db.sql(
        """
        SELECT COUNT(*) FROM `tabCH Bin Transfer Reason`
        WHERE source_bin_type IN %s OR target_bin_type IN %s
        """,
        (bt_tuple, bt_tuple),
    )[0][0]
    if reason_count:
        frappe.db.sql(
            """
            DELETE FROM `tabCH Bin Transfer Reason`
            WHERE source_bin_type IN %s OR target_bin_type IN %s
            """,
            (bt_tuple, bt_tuple),
        )
        print(f"[purge] Deleted {reason_count} CH Bin Transfer Reason rows")

    # 9. Null out pointers from masters that could still reference the wh names.
    frappe.db.sql(
        "UPDATE `tabCH Store` SET warehouse=NULL WHERE warehouse IN %s",
        (wh_tuple,),
    )
    frappe.db.sql(
        "UPDATE `tabCH Store` SET warehouse_group=NULL WHERE warehouse_group IN %s",
        (wh_tuple,),
    )
    frappe.db.sql(
        """
        UPDATE `tabCH Store Zone`
        SET source_warehouse=NULL
        WHERE source_warehouse IN %s
        """,
        (wh_tuple,),
    )

    # 10. Finally, drop the warehouses themselves. Skip the framework rename
    #     machinery — go straight to SQL because we already pre-cleared every
    #     known FK.
    for w in warehouses:
        frappe.db.sql("DELETE FROM tabWarehouse WHERE name=%s", w)
    print(f"[purge] Deleted {len(warehouses)} Warehouse rows")

    # 11. Repost Bin.actual_qty for surviving (item, warehouse) pairs from
    #     the remaining SLE rows.
    reposted = 0
    for item, wh in affected:
        qty = frappe.db.sql(
            """
            SELECT IFNULL(SUM(actual_qty), 0)
            FROM `tabStock Ledger Entry`
            WHERE item_code=%s AND warehouse=%s
            """,
            (item, wh),
        )[0][0]
        updated = frappe.db.sql(
            """
            UPDATE tabBin
            SET actual_qty=%s,
                projected_qty = (
                    %s + IFNULL(indented_qty,0) + IFNULL(ordered_qty,0)
                    + IFNULL(planned_qty,0)
                    - IFNULL(reserved_qty,0) - IFNULL(reserved_qty_for_production,0)
                    - IFNULL(reserved_qty_for_sub_contract,0)
                )
            WHERE item_code=%s AND warehouse=%s
            """,
            (qty, qty, item, wh),
        )
        if updated:
            reposted += 1
    print(f"[purge] Reposted {reposted} surviving Bin rows")

    frappe.db.commit()
    print("[purge] Done.")
