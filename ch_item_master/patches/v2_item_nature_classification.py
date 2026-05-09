# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Patch: classify every existing CH Sub Category with an item_nature value.

Rules (in order — first match wins):
  1. Sub-cat already has item_nature set                  -> skip
  2. Sub-cat is referenced by any CH Warranty Plan
     as service_item's sub-category                       -> Subscription
  3. All linked items have is_stock_item = 0              -> Service
  4. Sub-cat has at least one Variant-type spec           -> Variant Template
  5. Parent Category had allow_custom_item_name = 1       -> Simple Custom-Named
  6. Default                                              -> Simple Auto-Named

Also drops the now-removed CH Category.allow_custom_item_name column if it
still exists in the schema (safe — checked before drop).
"""

import frappe


def execute():
    if not frappe.db.has_column("CH Sub Category", "item_nature"):
        # Schema sync hasn't created the column yet; skip and let post-sync run again.
        return

    legacy_custom_categories = _legacy_custom_name_categories()
    subscription_subcats = _subscription_subcats()

    sub_cats = frappe.db.get_all(
        "CH Sub Category",
        fields=["name", "category", "item_nature"],
    )

    classified = {"Subscription": 0, "Service": 0, "Variant Template": 0,
                  "Simple Custom-Named": 0, "Simple Auto-Named": 0, "skipped": 0}

    for sc in sub_cats:
        if sc.item_nature:
            classified["skipped"] += 1
            continue

        nature = _classify_one(sc, legacy_custom_categories, subscription_subcats)
        # Companion field defaults derived from the chosen nature
        defaults = _companion_defaults(nature)

        update = {"item_nature": nature, **defaults}
        frappe.db.set_value("CH Sub Category", sc.name, update,
                            update_modified=False)
        classified[nature] = classified.get(nature, 0) + 1

    frappe.db.commit()

    # Drop legacy CH Category column if still present
    if frappe.db.has_column("CH Category", "allow_custom_item_name"):
        try:
            frappe.db.sql_ddl(
                "ALTER TABLE `tabCH Category` DROP COLUMN `allow_custom_item_name`"
            )
            frappe.db.commit()
        except Exception:
            # Column drop is best-effort — schema sync will retry
            pass

    print(f"[ch_item_master] item_nature classification done: {classified}")


def _classify_one(sc, legacy_custom_categories, subscription_subcats):
    if sc.name in subscription_subcats:
        return "Subscription"

    items = frappe.db.get_all(
        "Item",
        filters={"ch_sub_category": sc.name},
        fields=["is_stock_item"],
        limit=200,
    )
    if items and all((i.is_stock_item or 0) == 0 for i in items):
        return "Service"

    has_variant_spec = frappe.db.exists(
        "CH Sub Category Spec",
        {"parent": sc.name, "parenttype": "CH Sub Category", "is_variant": 1},
    )
    if has_variant_spec:
        return "Variant Template"

    if sc.category in legacy_custom_categories:
        return "Simple Custom-Named"

    return "Simple Auto-Named"


def _companion_defaults(nature):
    if nature == "Service":
        return {"is_stock_item_default": 0, "default_uom": "Hour"}
    if nature == "Subscription":
        return {"is_stock_item_default": 0, "default_uom": "Month"}
    if nature == "Asset / Capital":
        return {"is_stock_item_default": 1, "default_uom": "Nos",
                "serial_required": 1}
    # Stock natures — leave UOM blank so existing items aren't overwritten;
    # default Nos for newly created items will be picked from item.py fallback.
    return {"is_stock_item_default": 1}


def _legacy_custom_name_categories():
    """Return set of CH Category names that previously had
    allow_custom_item_name = 1 (column may have been dropped by now)."""
    if not frappe.db.has_column("CH Category", "allow_custom_item_name"):
        return set()
    rows = frappe.db.sql(
        """SELECT name FROM `tabCH Category`
           WHERE allow_custom_item_name = 1""",
        as_dict=True,
    )
    return {r.name for r in rows}


def _subscription_subcats():
    """Return set of CH Sub Category names that are referenced by an active
    CH Warranty Plan as the service_item's sub-category."""
    if not frappe.db.exists("DocType", "CH Warranty Plan"):
        return set()
    rows = frappe.db.sql(
        """SELECT DISTINCT i.ch_sub_category AS subcat
           FROM `tabCH Warranty Plan` wp
           JOIN `tabItem` i ON i.name = wp.service_item
           WHERE wp.service_item IS NOT NULL
             AND i.ch_sub_category IS NOT NULL""",
        as_dict=True,
    )
    return {r.subcat for r in rows if r.subcat}
