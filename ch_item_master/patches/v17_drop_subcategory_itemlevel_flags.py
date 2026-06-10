# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe


_COLUMNS_TO_DROP = (
    "serial_required",
    "batch_required",
    "has_expiry",
    "weight_per_unit_required",
)


def execute():
    """Drop CH Sub Category flags that were intentionally moved to Item level.

    Idempotent/safe: every column is existence-checked before ALTER.
    """
    table = "tabCH Sub Category"
    dropped = []

    for col in _COLUMNS_TO_DROP:
        if not frappe.db.has_column("CH Sub Category", col):
            continue
        frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{col}`")
        dropped.append(col)

    if dropped:
        frappe.db.commit()
        print(f"[ch_item_master] dropped CH Sub Category columns: {dropped}")
