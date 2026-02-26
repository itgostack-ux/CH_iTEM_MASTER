# Copyright (c) 2026, GoStack and contributors
# Transaction pricing hooks — auto-apply CH Item Price / Offer on save
#
# Applies to: Sales Order, Sales Invoice, Quotation, Delivery Note,
#             Purchase Order, Purchase Invoice
#
# Logic:
#   For each item row, if a CH Item Price exists for the item + channel
#   (derived from pos_profile / naming convention) on today's date,
#   validate/fill rate.  Does NOT overwrite if user already manually set price.

import frappe
from frappe.utils import getdate, nowdate

# Map transaction doctypes → the channel they represent
_DOCTYPE_CHANNEL_MAP = {
    "Sales Invoice":   "POS",
    "Sales Order":     "POS",
    "Quotation":       "Website",
    "Delivery Note":   "POS",
    "Purchase Order":  None,   # purchase has no channel pricing — skip
    "Purchase Invoice": None,
}


def apply_ch_pricing(doc, method=None):
    """Doc event hook: called on before_save for sales/purchase documents.

    For each item row:
      1. Determine channel from doctype.
      2. Look up active CH Item Price for that item + channel.
      3. If found and item row rate is 0 or blank, fill it in.
      4. Attach active offer label to item description (non-destructive).

    This is intentionally soft — it never overwrites a manually entered rate.
    """
    channel = _DOCTYPE_CHANNEL_MAP.get(doc.doctype)
    if not channel:
        return

    today = getdate(nowdate())

    for row in doc.get("items") or []:
        if not row.item_code:
            continue

        # Only fill if rate is blank/zero (user hasn't manually set it)
        if row.rate:
            continue

        price = _get_active_ch_price(row.item_code, channel, today)
        if not price:
            continue

        row.rate = price.get("selling_price") or 0

        # Append offer label to item notes if present
        offer = price.get("offer_label")
        if offer and offer.strip():
            existing_note = row.get("item_description") or ""
            if offer not in existing_note:
                row.item_description = (existing_note + f" [{offer}]").strip()


def _get_active_ch_price(item_code, channel, as_of):
    """Return the best active CH Item Price dict for item + channel on as_of date."""
    prices = frappe.get_all(
        "CH Item Price",
        filters={
            "item_code": item_code,
            "channel": channel,
            "status": ("in", ["Active", "Scheduled"]),
            "effective_from": ("<=", str(as_of)),
        },
        fields=["selling_price", "mrp", "mop", "effective_to"],
        order_by="effective_from desc",
        limit=1,
    )

    if not prices:
        return None

    pr = prices[0]
    if pr.effective_to and getdate(pr.effective_to) < as_of:
        return None

    # Fetch best offer label
    from ch_item_master.ch_item_master.ready_reckoner_api import _compute_best_offer
    from frappe.utils import now_datetime

    now_str = str(now_datetime())
    offers = frappe.get_all(
        "CH Item Offer",
        filters=[
            ["item_code", "=", item_code],
            ["status", "=", "Active"],
            ["approval_status", "=", "Approved"],
            ["start_date", "<=", now_str],
            ["end_date", ">=", now_str],
            ["channel", "in", [channel, ""]],
        ],
        fields=["offer_type", "value_type", "value", "priority", "stackable"],
    )
    best = _compute_best_offer(offers)

    return {
        "selling_price": pr.selling_price,
        "mrp": pr.mrp,
        "mop": pr.mop,
        "offer_label": best.get("label", ""),
    }
