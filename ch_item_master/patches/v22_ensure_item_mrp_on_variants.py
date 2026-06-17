"""Ensure item-level MRP is available on item variants.

The custom MRP field is mandatory for stock items at controller level, not at
metadata level. ERPNext only copies non-required custom fields to variants when
they are present in Item Variant Settings, so existing sites need both the copy
setting and a data backfill for variants already created with blank MRP.
"""

import frappe
from frappe.utils import flt


def execute():
    if not frappe.db.exists("DocType", "Item"):
        return

    if not _has_column("tabItem", "ch_item_mrp"):
        print("[v22_ensure_item_mrp_on_variants] Item.ch_item_mrp is missing; skipping.")
        return

    from ch_item_master.setup import setup_item_variant_settings

    setup_item_variant_settings()
    updated, skipped = _backfill_variant_mrp()

    frappe.db.commit()
    frappe.clear_cache(doctype="Item")
    print(
        "[v22_ensure_item_mrp_on_variants] Updated {0} variants, skipped {1}.".format(
            updated,
            skipped,
        )
    )


def _backfill_variant_mrp() -> tuple[int, int]:
    rows = frappe.db.sql(
        """
        SELECT
            variant.name,
            variant.item_code,
            variant.variant_of,
            template.ch_item_mrp AS template_mrp
        FROM `tabItem` variant
        LEFT JOIN `tabItem` template ON template.name = variant.variant_of
        WHERE IFNULL(variant.variant_of, '') != ''
          AND IFNULL(variant.is_stock_item, 0) = 1
          AND IFNULL(variant.disabled, 0) = 0
          AND IFNULL(variant.ch_item_mrp, 0) <= 0
        """,
        as_dict=True,
    )

    updated = 0
    skipped = 0

    for row in rows:
        mrp = _best_mrp(row.name, row.item_code, "Active")
        if not mrp:
            mrp = _best_mrp(row.name, row.item_code, "Scheduled")
        if not mrp:
            mrp = flt(row.template_mrp)

        if not mrp:
            skipped += 1
            continue

        frappe.db.set_value("Item", row.name, "ch_item_mrp", mrp, update_modified=False)
        updated += 1

    return updated, skipped


def _best_mrp(item_name: str, item_code: str | None, status: str) -> float:
    item_codes = [item_name]
    if item_code and item_code != item_name:
        item_codes.append(item_code)

    result = frappe.db.sql(
        """
        SELECT MAX(mrp)
        FROM `tabCH Item Price`
        WHERE item_code IN %(item_codes)s
          AND status = %(status)s
          AND mrp > 0
        """,
        {"item_codes": tuple(item_codes), "status": status},
    )
    return flt(result[0][0]) if result and result[0][0] else 0


def _has_column(table_name: str, column: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (table_name, column),
        )
    )
