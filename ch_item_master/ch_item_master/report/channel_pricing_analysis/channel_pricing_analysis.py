# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Channel Pricing Analysis — Script Report

For ASM / RSM roles — channel-wise pricing comparison:
- Each row = one item, columns for each active channel
- Highlights price gaps between channels
- Shows which channels are missing prices
- Enables channel-specific pricing decisions

Designed to show side-by-side channel pricing for quick comparison.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    company = filters.get("company")
    if not company:
        frappe.throw(_("Please select a Company"))

    channels = frappe.get_all("CH Price Channel",
                              filters={"is_active": 1},
                              fields=["name", "channel_name"],
                              order_by="channel_name")

    columns = get_columns(channels)
    data = get_data(filters, channels)
    chart = get_chart(data, channels)
    summary = get_summary(data, channels)
    return columns, data, None, chart, summary


def get_columns(channels):
    cols = [
        {"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link",
         "options": "Item", "width": 160},
        {"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
        {"fieldname": "sub_category", "label": _("Sub Category"), "fieldtype": "Link",
         "options": "CH Sub Category", "width": 130},
        {"fieldname": "manufacturer", "label": _("Manufacturer"), "fieldtype": "Link",
         "options": "Manufacturer", "width": 110},
        {"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link",
         "options": "Brand", "width": 90},
    ]

    for ch in channels:
        cols.append({
            "fieldname": "sp_{}".format(ch.name.replace(" ", "_").replace("-", "_").lower()),
            "label": _("{} SP".format(ch.channel_name)),
            "fieldtype": "Currency",
            "width": 110,
        })

    cols.extend([
        {"fieldname": "min_sp", "label": _("Min SP"), "fieldtype": "Currency", "width": 90},
        {"fieldname": "max_sp", "label": _("Max SP"), "fieldtype": "Currency", "width": 90},
        {"fieldname": "spread_pct", "label": _("Spread %"), "fieldtype": "Percent", "width": 90},
        {"fieldname": "channels_priced", "label": _("Channels"), "fieldtype": "Data", "width": 80},
        {"fieldname": "status_flag", "label": _("Flag"), "fieldtype": "Data", "width": 100},
    ])

    return cols


def get_data(filters, channels):
    company = filters.get("company")

    # Build item filter
    item_conds = ["i.disabled = 0", "i.has_variants = 0"]
    vals = {}

    if filters.get("category"):
        item_conds.append("""i.ch_sub_category IN
            (SELECT name FROM `tabCH Sub Category` WHERE category = %(category)s)""")
        vals["category"] = filters["category"]
    if filters.get("sub_category"):
        item_conds.append("i.ch_sub_category = %(sub_category)s")
        vals["sub_category"] = filters["sub_category"]
    if filters.get("manufacturer"):
        item_conds.append("i.ch_manufacturer = %(manufacturer)s")
        vals["manufacturer"] = filters["manufacturer"]

    item_where = " AND ".join(item_conds)

    # Only items that have at least one active price in this company
    items = frappe.db.sql("""
        SELECT DISTINCT i.name as item_code, i.item_name,
            i.ch_sub_category as sub_category,
            i.ch_manufacturer as manufacturer,
            i.brand
        FROM `tabItem` i
        JOIN `tabCH Item Price` p ON p.item_code = i.name
            AND p.company = %(company)s AND p.status = 'Active'
        WHERE {w}
        ORDER BY i.ch_sub_category, i.item_name
        LIMIT 500
    """.format(w=item_where), {**vals, "company": company}, as_dict=True)

    # Get all active prices for these items
    if items:
        item_codes = [it.item_code for it in items]
        prices = frappe.db.sql("""
            SELECT item_code, channel, selling_price
            FROM `tabCH Item Price`
            WHERE item_code IN %(items)s
                AND company = %(company)s
                AND status = 'Active'
        """, {"items": item_codes, "company": company}, as_dict=True)

        # Index: item_code → channel → selling_price
        price_map = {}
        for p in prices:
            price_map.setdefault(p.item_code, {})[p.channel] = flt(p.selling_price)

    data = []
    for item in items:
        row = {
            "item_code": item.item_code,
            "item_name": item.item_name,
            "sub_category": item.sub_category,
            "manufacturer": item.manufacturer,
            "brand": item.brand,
        }

        ch_prices = price_map.get(item.item_code, {})
        sp_values = []
        priced_count = 0

        for ch in channels:
            key = "sp_{}".format(ch.name.replace(" ", "_").replace("-", "_").lower())
            sp = ch_prices.get(ch.name, 0)
            row[key] = sp if sp else None
            if sp:
                sp_values.append(sp)
                priced_count += 1

        min_sp = min(sp_values) if sp_values else 0
        max_sp = max(sp_values) if sp_values else 0
        spread = round((max_sp - min_sp) / min_sp * 100, 1) if min_sp > 0 else 0

        row["min_sp"] = min_sp
        row["max_sp"] = max_sp
        row["spread_pct"] = spread
        row["channels_priced"] = "{}/{}".format(priced_count, len(channels))

        if priced_count < len(channels):
            row["status_flag"] = "⚠ Missing"
        elif spread > 20:
            row["status_flag"] = "⚠ High Spread"
        else:
            row["status_flag"] = "✅ OK"

        data.append(row)

    return data


def get_chart(data, channels):
    if not data or not channels:
        return None

    # Average SP per channel
    ch_totals = {ch.name: [] for ch in channels}
    for row in data:
        for ch in channels:
            key = "sp_{}".format(ch.name.replace(" ", "_").replace("-", "_").lower())
            if row.get(key):
                ch_totals[ch.name].append(row[key])

    labels = [ch.channel_name for ch in channels]
    values = [round(sum(ch_totals[ch.name]) / len(ch_totals[ch.name]), 0)
              if ch_totals[ch.name] else 0 for ch in channels]

    return {
        "data": {
            "labels": labels,
            "datasets": [{"name": _("Avg Selling Price"), "values": values}],
        },
        "type": "bar",
        "colors": ["#3182ce"],
    }


def get_summary(data, channels):
    total = len(data)
    missing = sum(1 for r in data if "Missing" in (r.get("status_flag") or ""))
    high_spread = sum(1 for r in data if "Spread" in (r.get("status_flag") or ""))

    return [
        {"value": total, "indicator": "blue", "label": _("Items Analysed"), "datatype": "Int"},
        {"value": missing, "indicator": "red" if missing else "green",
         "label": _("Missing Channel Prices"), "datatype": "Int"},
        {"value": high_spread, "indicator": "orange" if high_spread else "green",
         "label": _("High Price Spread (>20%)"), "datatype": "Int"},
        {"value": len(channels), "indicator": "blue",
         "label": _("Active Channels"), "datatype": "Int"},
    ]
