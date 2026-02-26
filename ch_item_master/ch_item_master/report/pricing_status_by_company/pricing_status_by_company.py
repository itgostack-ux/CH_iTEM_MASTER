# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Pricing Status by Company — Script Report

For Store Managers — operational view of all prices for their company:
- Item-level listing with price status, channel, MRP, SP, margin, validity
- Quick filters for status, channel, category
- Highlights expiring soon, draft, and missing prices
- Shows effective dates and approval info
"""

import frappe
from frappe import _
from frappe.utils import today, getdate, date_diff, flt


def execute(filters=None):
    filters = filters or {}
    if not filters.get("company"):
        frappe.throw(_("Please select a Company"))

    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link",
         "options": "Item", "width": 160},
        {"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
        {"fieldname": "sub_category", "label": _("Sub Category"), "fieldtype": "Link",
         "options": "CH Sub Category", "width": 130},
        {"fieldname": "channel", "label": _("Channel"), "fieldtype": "Link",
         "options": "CH Price Channel", "width": 110},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 80},
        {"fieldname": "mrp", "label": _("MRP"), "fieldtype": "Currency", "width": 90},
        {"fieldname": "selling_price", "label": _("Selling Price"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "margin_pct", "label": _("Margin %"), "fieldtype": "Percent", "width": 90},
        {"fieldname": "effective_from", "label": _("From"), "fieldtype": "Date", "width": 100},
        {"fieldname": "effective_to", "label": _("To"), "fieldtype": "Date", "width": 100},
        {"fieldname": "days_remaining", "label": _("Days Left"), "fieldtype": "Int", "width": 80},
        {"fieldname": "approved_by", "label": _("Approved By"), "fieldtype": "Link",
         "options": "User", "width": 120},
        {"fieldname": "flag", "label": _("Flag"), "fieldtype": "Data", "width": 110},
        {"fieldname": "price_name", "label": _("Price ID"), "fieldtype": "Link",
         "options": "CH Item Price", "width": 130},
    ]


def get_data(filters):
    conds = ["p.company = %(company)s"]
    vals = {"company": filters["company"]}

    if filters.get("channel"):
        conds.append("p.channel = %(channel)s")
        vals["channel"] = filters["channel"]

    if filters.get("status"):
        conds.append("p.status = %(status)s")
        vals["status"] = filters["status"]

    if filters.get("category"):
        conds.append("""i.ch_sub_category IN
            (SELECT name FROM `tabCH Sub Category` WHERE category = %(category)s)""")
        vals["category"] = filters["category"]

    where = " AND ".join(conds)
    cur_date = getdate(today())

    rows = frappe.db.sql("""
        SELECT
            p.name as price_name,
            p.item_code,
            p.item_name,
            i.ch_sub_category as sub_category,
            p.channel,
            p.status,
            p.mrp,
            p.selling_price,
            p.effective_from,
            p.effective_to,
            p.approved_by
        FROM `tabCH Item Price` p
        LEFT JOIN `tabItem` i ON i.name = p.item_code
        WHERE {w}
        ORDER BY p.status, p.channel, p.item_name
        LIMIT 1000
    """.format(w=where), vals, as_dict=True)

    data = []
    for r in rows:
        # Margin
        margin_pct = round((1 - r.selling_price / r.mrp) * 100, 1) if r.mrp and r.mrp > 0 else 0

        # Days remaining
        days_left = None
        if r.effective_to:
            days_left = date_diff(r.effective_to, cur_date)

        # Flag
        flag = ""
        if r.status == "Draft":
            flag = "📝 Draft"
        elif r.status == "Expired":
            flag = "❌ Expired"
        elif days_left is not None and days_left <= 3 and days_left >= 0:
            flag = "🔴 Expiring Soon"
        elif days_left is not None and days_left <= 7 and days_left >= 0:
            flag = "🟡 Expiring"
        elif r.status == "Active":
            flag = "✅ OK"

        data.append({
            "price_name": r.price_name,
            "item_code": r.item_code,
            "item_name": r.item_name,
            "sub_category": r.sub_category,
            "channel": r.channel,
            "status": r.status,
            "mrp": r.mrp,
            "selling_price": r.selling_price,
            "margin_pct": margin_pct,
            "effective_from": r.effective_from,
            "effective_to": r.effective_to,
            "days_remaining": days_left,
            "approved_by": r.approved_by,
            "flag": flag,
        })

    return data


def get_chart(data):
    if not data:
        return None

    status_counts = {}
    for r in data:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    labels = list(status_counts.keys())
    colors_map = {"Active": "#38a169", "Draft": "#a0aec0", "Scheduled": "#3182ce", "Expired": "#e53e3e"}

    return {
        "data": {
            "labels": labels,
            "datasets": [{"name": _("Prices"), "values": [status_counts[l] for l in labels]}],
        },
        "type": "donut",
        "colors": [colors_map.get(l, "#718096") for l in labels],
    }


def get_summary(data):
    total = len(data)
    active = sum(1 for r in data if r["status"] == "Active")
    drafts = sum(1 for r in data if r["status"] == "Draft")
    expiring_soon = sum(1 for r in data if "Expiring" in (r.get("flag") or ""))
    avg_margin = round(sum(r["margin_pct"] for r in data) / total, 1) if total else 0

    return [
        {"value": total, "indicator": "blue", "label": _("Total Prices"), "datatype": "Int"},
        {"value": active, "indicator": "green", "label": _("Active"), "datatype": "Int"},
        {"value": drafts, "indicator": "grey", "label": _("Drafts"), "datatype": "Int"},
        {"value": expiring_soon, "indicator": "orange" if expiring_soon else "green",
         "label": _("Expiring Soon"), "datatype": "Int"},
        {"value": avg_margin, "indicator": "blue",
         "label": _("Avg Margin %"), "datatype": "Percent"},
    ]
