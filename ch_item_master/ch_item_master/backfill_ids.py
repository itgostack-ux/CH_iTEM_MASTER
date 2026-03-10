# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Cascade denormalized parent IDs across the CH master hierarchy.

Primary IDs (brand_id, category_id, model_id, etc.) are assigned once
at creation time by before_insert / autoname hooks — they are immutable
and never need updating.

Denormalized IDs are convenience copies of parent IDs kept on child
records for fast API lookups (e.g. CH Sub Category.category_id is a copy
of CH Category.category_id).  These are normally populated by each
DocType's ``validate → _populate_ids()`` method on every save.

This module provides bulk SQL cascades as a safety net — useful after
``bench migrate`` or bulk Data Import where child records might reference
parents whose IDs weren't yet available at import time.

Runs automatically on:
  - ``bench migrate`` (registered in after_migrate)
  - After Data Import completion (doc_event on Data Import)
"""

import frappe
from frappe import _


# ── Denormalized / cascaded ID population ────────────────────────────────────
# These are numeric ID copies of parent records, kept for API performance.
# They are NOT master IDs — just derived copies.

def _cascade_category_ids():
    """CH Category.item_group_id ← Item Group.item_group_id."""
    frappe.db.sql("""
        UPDATE `tabCH Category` c
        INNER JOIN `tabItem Group` ig ON ig.name = c.item_group
        SET c.item_group_id = ig.item_group_id
        WHERE IFNULL(c.item_group_id, 0) != IFNULL(ig.item_group_id, 0)
          AND c.item_group IS NOT NULL AND c.item_group != ''
    """)


def _cascade_sub_category_ids():
    """CH Sub Category: category_id ← CH Category, item_group_id ← Item Group."""
    frappe.db.sql("""
        UPDATE `tabCH Sub Category` sc
        INNER JOIN `tabCH Category` c ON c.name = sc.category
        SET sc.category_id = c.category_id
        WHERE IFNULL(sc.category_id, 0) != IFNULL(c.category_id, 0)
          AND sc.category IS NOT NULL AND sc.category != ''
    """)
    frappe.db.sql("""
        UPDATE `tabCH Sub Category` sc
        INNER JOIN `tabItem Group` ig ON ig.name = sc.item_group
        SET sc.item_group_id = ig.item_group_id
        WHERE IFNULL(sc.item_group_id, 0) != IFNULL(ig.item_group_id, 0)
          AND sc.item_group IS NOT NULL AND sc.item_group != ''
    """)


def _cascade_model_ids():
    """CH Model: brand_id, manufacturer_id, sub_category_id, category_id, item_group_id."""
    frappe.db.sql("""
        UPDATE `tabCH Model` m
        INNER JOIN `tabBrand` b ON b.name = m.brand
        SET m.brand_id = b.brand_id
        WHERE IFNULL(m.brand_id, 0) != IFNULL(b.brand_id, 0)
          AND m.brand IS NOT NULL AND m.brand != ''
    """)
    frappe.db.sql("""
        UPDATE `tabCH Model` m
        INNER JOIN `tabManufacturer` mf ON mf.name = m.manufacturer
        SET m.manufacturer_id = mf.manufacturer_id
        WHERE IFNULL(m.manufacturer_id, 0) != IFNULL(mf.manufacturer_id, 0)
          AND m.manufacturer IS NOT NULL AND m.manufacturer != ''
    """)
    frappe.db.sql("""
        UPDATE `tabCH Model` m
        INNER JOIN `tabCH Sub Category` sc ON sc.name = m.sub_category
        SET m.sub_category_id = sc.sub_category_id,
            m.category_id     = sc.category_id,
            m.item_group_id   = sc.item_group_id
        WHERE (IFNULL(m.sub_category_id, 0) != IFNULL(sc.sub_category_id, 0)
            OR IFNULL(m.category_id, 0) != IFNULL(sc.category_id, 0)
            OR IFNULL(m.item_group_id, 0) != IFNULL(sc.item_group_id, 0))
          AND m.sub_category IS NOT NULL AND m.sub_category != ''
    """)


def _cascade_brand_manufacturer_ids():
    """Brand child table: CH Brand Manufacturer.manufacturer_id."""
    frappe.db.sql("""
        UPDATE `tabCH Brand Manufacturer` bm
        INNER JOIN `tabManufacturer` mf ON mf.name = bm.manufacturer
        SET bm.manufacturer_id = mf.manufacturer_id
        WHERE IFNULL(bm.manufacturer_id, 0) != IFNULL(mf.manufacturer_id, 0)
          AND bm.manufacturer IS NOT NULL AND bm.manufacturer != ''
    """)


def _cascade_item_ids():
    """Item: ch_brand_id, ch_manufacturer_id, ch_sub_category_id, ch_model_id,
       ch_category_id, ch_item_group_id."""
    frappe.db.sql("""
        UPDATE `tabItem` i
        INNER JOIN `tabCH Model` m ON m.name = i.ch_model
        SET i.ch_model_id = m.model_id,
            i.ch_brand_id = IFNULL(m.brand_id, 0),
            i.ch_manufacturer_id = IFNULL(m.manufacturer_id, 0)
        WHERE (IFNULL(i.ch_model_id, 0) != IFNULL(m.model_id, 0)
            OR IFNULL(i.ch_brand_id, 0) != IFNULL(m.brand_id, 0)
            OR IFNULL(i.ch_manufacturer_id, 0) != IFNULL(m.manufacturer_id, 0))
          AND i.ch_model IS NOT NULL AND i.ch_model != ''
    """)
    frappe.db.sql("""
        UPDATE `tabItem` i
        INNER JOIN `tabCH Sub Category` sc ON sc.name = i.ch_sub_category
        SET i.ch_sub_category_id = sc.sub_category_id
        WHERE IFNULL(i.ch_sub_category_id, 0) != IFNULL(sc.sub_category_id, 0)
          AND i.ch_sub_category IS NOT NULL AND i.ch_sub_category != ''
    """)
    frappe.db.sql("""
        UPDATE `tabItem` i
        INNER JOIN `tabCH Category` c ON c.name = i.ch_category
        SET i.ch_category_id = c.category_id
        WHERE IFNULL(i.ch_category_id, 0) != IFNULL(c.category_id, 0)
          AND i.ch_category IS NOT NULL AND i.ch_category != ''
    """)
    frappe.db.sql("""
        UPDATE `tabItem` i
        INNER JOIN `tabItem Group` ig ON ig.name = i.item_group
        SET i.ch_item_group_id = ig.item_group_id
        WHERE IFNULL(i.ch_item_group_id, 0) != IFNULL(ig.item_group_id, 0)
          AND i.item_group IS NOT NULL AND i.item_group != ''
    """)


def _run_all_cascades():
    """Run all cascades in parent-first order."""
    _cascade_category_ids()
    _cascade_sub_category_ids()
    _cascade_model_ids()
    _cascade_brand_manufacturer_ids()
    _cascade_item_ids()


# ── Hooks ─────────────────────────────────────────────────────────────────────

def backfill_ids_after_migrate():
    """after_migrate hook — cascades denormalized parent IDs.

    Primary IDs are immutable (set at creation). This only syncs the
    derived copies (e.g. CH Sub Category.category_id from CH Category).
    """
    try:
        _run_all_cascades()
        frappe.db.commit()
    except Exception:
        frappe.log_error("backfill_ids_after_migrate", frappe.get_traceback())


def on_data_import_complete(doc, method=None):
    """Triggered after a Data Import finishes (on_update_after_submit).

    Cascades denormalized parent IDs so that imported child records
    pick up their parent's IDs for API use.
    """
    if doc.status != "Success":
        return

    # Only cascade for DocTypes in our hierarchy
    relevant = {
        "Item Group", "Manufacturer", "Brand", "Customer",
        "CH Category", "CH Sub Category", "CH Model", "Item",
        "CH Feature", "CH Feature Group", "CH Warranty Plan",
        "CH Customer Device", "CH Loyalty Transaction",
    }
    if doc.reference_doctype not in relevant:
        return

    try:
        _run_all_cascades()
        frappe.db.commit()
    except Exception:
        frappe.log_error("on_data_import_complete", frappe.get_traceback())
