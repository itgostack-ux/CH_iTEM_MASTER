# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Shared serial-number extraction utility.

Every custom app that needs to read serial numbers from a document item
row should import from here instead of reimplementing the extraction
logic.  This handles:

  1. Legacy ``serial_no`` text field (newline-separated)
  2. ERPNext v16 ``serial_and_batch_bundle`` reference
  3. Cancel-time caching (parent_doc._cached_serial_nos) where the
     bundle reference may already be cleared before hooks run

Usage::

    from ch_item_master.ch_item_master.serial_utils import get_serial_nos_from_item

    serials = get_serial_nos_from_item(item_row)
    serials = get_serial_nos_from_item(item_row, parent_doc=doc)  # cancel-safe
"""


def get_serial_nos_from_item(item, parent_doc=None):
    """Extract serial numbers from an item row (any transaction doctype).

    Args:
        item: A child-table row (Sales Invoice Item, Stock Entry Detail,
              Delivery Note Item, Purchase Receipt Item, etc.).
        parent_doc: Optional parent document.  If it carries a
              ``_cached_serial_nos`` dict (keyed by item row name),
              those are returned first — needed during on_cancel when
              ERPNext has already cleared the serial_and_batch_bundle.

    Returns:
        list[str]: Serial numbers (may be empty).
    """
    # 1. Cancel-time cache (set by the owning app before super().on_cancel)
    if parent_doc and hasattr(parent_doc, "_cached_serial_nos"):
        cached = parent_doc._cached_serial_nos.get(item.name, [])
        if cached:
            return cached

    # 2. Legacy serial_no text field (newline-separated list)
    serial_nos = (item.get("serial_no") or "").strip()
    if serial_nos:
        return [s.strip() for s in serial_nos.split("\n") if s.strip()]

    # 3. ERPNext v16 Serial and Batch Bundle
    bundle = item.get("serial_and_batch_bundle")
    if bundle:
        try:
            from erpnext.stock.serial_batch_bundle import get_serial_nos
            return get_serial_nos(bundle) or []
        except Exception:
            # Fallback: direct DB query if the helper is unavailable
            import frappe
            entries = frappe.get_all(
                "Serial and Batch Entry",
                filters={"parent": bundle},
                pluck="serial_no",
            )
            return [sn for sn in entries if sn]

    return []
