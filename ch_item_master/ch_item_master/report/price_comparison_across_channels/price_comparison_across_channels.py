# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Price Comparison Across Channels — Script Report

Shows active prices for each item side-by-side across all channels,
with MRP, MOP, and Selling Price per channel. Helps spot pricing
inconsistencies and channel-level margin differences.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}

    # Get all active channels
    channels = frappe.get_all(
        "CH Price Channel",
        filters={"is_active": 1},
        fields=["name", "channel_name"],
        order_by="channel_name",
    )

    columns = get_columns(channels)
    data = get_data(filters, channels)
    return columns, data


def get_columns(channels):
    cols = [
        {"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link",
         "options": "Item", "width": 140},
        {"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 220},
        {"fieldname": "mrp", "label": _("MRP"), "fieldtype": "Currency", "width": 100},
    ]

    for ch in channels:
        key = frappe.scrub(ch.name)
        cols.append({
            "fieldname": f"sp_{key}",
            "label": f"{ch.channel_name} SP",
            "fieldtype": "Currency",
            "width": 110,
        })

    cols.append({
        "fieldname": "min_sp", "label": _("Min SP"), "fieldtype": "Currency", "width": 100,
    })
    cols.append({
        "fieldname": "max_sp", "label": _("Max SP"), "fieldtype": "Currency", "width": 100,
    })
    cols.append({
        "fieldname": "spread", "label": _("Spread"), "fieldtype": "Currency", "width": 100,
    })

    return cols


def get_data(filters, channels):
    conditions = []
    values = {}

    if filters.get("category"):
        conditions.append("i.ch_category = %(category)s")
        values["category"] = filters["category"]

    if filters.get("sub_category"):
        conditions.append("i.ch_sub_category = %(sub_category)s")
        values["sub_category"] = filters["sub_category"]

    if filters.get("company"):
        conditions.append("p.company = %(company)s")
        values["company"] = filters["company"]

    where = " AND ".join(conditions) if conditions else "1=1"

    # Fetch all active prices grouped by item
    prices = frappe.db.sql("""
        SELECT
            p.item_code,
            i.item_name,
            p.channel,
            p.mrp,
            p.selling_price
        FROM `tabCH Item Price` p
        INNER JOIN `tabItem` i ON i.item_code = p.item_code
        WHERE p.status = 'Active'
          AND {where}
        ORDER BY i.item_name
    """.format(where=where), values, as_dict=True)

    # Pivot: group by item_code
    from collections import OrderedDict
    items = OrderedDict()
    for row in prices:
        ic = row.item_code
        if ic not in items:
            items[ic] = {
                "item_code": ic,
                "item_name": row.item_name,
                "mrp": row.mrp or 0,
            }
        key = f"sp_{frappe.scrub(row.channel)}"
        items[ic][key] = row.selling_price or 0

    # Compute min/max/spread
    channel_keys = [f"sp_{frappe.scrub(ch.name)}" for ch in channels]
    result = []
    for item in items.values():
        sp_values = [item.get(k, 0) for k in channel_keys if item.get(k, 0) > 0]
        item["min_sp"] = min(sp_values) if sp_values else 0
        item["max_sp"] = max(sp_values) if sp_values else 0
        item["spread"] = item["max_sp"] - item["min_sp"]
        result.append(item)

    return result
