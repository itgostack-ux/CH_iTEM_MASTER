# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Executive Summary — Script Report

High-level overview for executives showing:
- KPIs grouped by Company, Category, or Channel
- Total items, total priced, coverage %, active offers
- Average MRP, SP, discount %, revenue potential
- Colour-coded health indicators
"""

import frappe
from frappe import _
from frappe.utils import today, flt


def execute(filters=None):
    filters = filters or {}
    group_by = filters.get("group_by", "Company")

    columns = get_columns(group_by)
    data = get_data(filters, group_by)
    chart = get_chart(data, group_by)
    summary = get_summary(data)
    return columns, data, None, chart, summary


def get_columns(group_by):
    label_map = {"Company": "Company", "Category": "Category", "Channel": "Channel"}
    link_map = {"Company": "Company", "Category": "CH Category", "Channel": "CH Price Channel"}
    first_col = {
        "fieldname": "group_label",
        "label": _(label_map.get(group_by, "Group")),
        "fieldtype": "Link" if group_by in link_map else "Data",
        "options": link_map.get(group_by, ""),
        "width": 180,
    }
    return [
        first_col,
        {"fieldname": "total_models", "label": _("Models"), "fieldtype": "Int", "width": 80},
        {"fieldname": "total_items", "label": _("Items"), "fieldtype": "Int", "width": 80},
        {"fieldname": "total_templates", "label": _("Templates"), "fieldtype": "Int", "width": 90},
        {"fieldname": "total_variants", "label": _("Variants"), "fieldtype": "Int", "width": 90},
        {"fieldname": "active_prices", "label": _("Active Prices"), "fieldtype": "Int", "width": 100},
        {"fieldname": "priced_items", "label": _("Items Priced"), "fieldtype": "Int", "width": 100},
        {"fieldname": "coverage_pct", "label": _("Coverage %"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "avg_mrp", "label": _("Avg MRP"), "fieldtype": "Currency", "width": 100},
        {"fieldname": "avg_sp", "label": _("Avg SP"), "fieldtype": "Currency", "width": 100},
        {"fieldname": "avg_discount_pct", "label": _("Avg Disc %"), "fieldtype": "Percent", "width": 90},
        {"fieldname": "active_offers", "label": _("Offers"), "fieldtype": "Int", "width": 70},
        {"fieldname": "health", "label": _("Health"), "fieldtype": "Data", "width": 80},
    ]


def get_data(filters, group_by):
    cur_date = today()
    company_filter = filters.get("company")

    if group_by == "Company":
        return _data_by_company(cur_date, company_filter)
    elif group_by == "Category":
        return _data_by_category(cur_date, company_filter)
    else:
        return _data_by_channel(cur_date, company_filter)


def _data_by_company(cur_date, company_filter):
    conds = []
    vals = {}
    if company_filter:
        conds.append("p.company = %(company)s")
        vals["company"] = company_filter

    price_where = " AND ".join(["p.status = 'Active'"] + conds) if conds else "p.status = 'Active'"

    companies = frappe.db.sql("""
        SELECT DISTINCT p.company as group_label
        FROM `tabCH Item Price` p
        WHERE {w}
        ORDER BY p.company
    """.format(w=price_where), vals, as_dict=True)

    if not companies and not company_filter:
        companies = [{"group_label": c.name} for c in
                      frappe.get_all("Company", filters={"is_group": 0}, fields=["name"])]

    data = []
    for row in companies:
        data.append(_build_row(row["group_label"], cur_date, company=row["group_label"]))
    return data


def _data_by_category(cur_date, company_filter):
    categories = frappe.get_all("CH Category", filters={"is_active": 1},
                                fields=["name"], order_by="name")
    data = []
    for cat in categories:
        data.append(_build_row(cat.name, cur_date, company=company_filter, category=cat.name))
    return data


def _data_by_channel(cur_date, company_filter):
    channels = frappe.get_all("CH Price Channel", filters={"is_active": 1},
                              fields=["name as channel_name"], order_by="name")
    data = []
    for ch in channels:
        data.append(_build_row(ch.channel_name, cur_date, company=company_filter, channel=ch.channel_name))
    return data


def _build_row(label, cur_date, company=None, category=None, channel=None):
    vals = {}
    item_conds = ["i.disabled = 0"]
    price_conds = ["p.status = 'Active'"]
    offer_conds = ["o.status = 'Active'"]

    if company:
        price_conds.append("p.company = %(company)s")
        offer_conds.append("o.company = %(company)s")
        vals["company"] = company

    if channel:
        price_conds.append("p.channel = %(channel)s")
        offer_conds.append("(o.channel = %(channel)s OR o.channel IS NULL OR o.channel = '')")
        vals["channel"] = channel

    if category:
        item_conds.append("""i.ch_sub_category IN
            (SELECT name FROM `tabCH Sub Category` WHERE category = %(category)s)""")
        vals["category"] = category

    item_where = " AND ".join(item_conds)
    price_where = " AND ".join(price_conds)
    offer_where = " AND ".join(offer_conds)

    # Items
    total_items = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabItem` i WHERE {w} AND has_variants = 0".format(w=item_where),
        vals)[0][0] or 0

    total_templates = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabItem` i WHERE {w} AND has_variants = 1".format(w=item_where),
        vals)[0][0] or 0

    # Models
    model_conds = ["m.is_active = 1"]
    if category:
        model_conds.append("m.sub_category IN (SELECT name FROM `tabCH Sub Category` WHERE category = %(category)s)")
    total_models = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabCH Model` m WHERE {w}".format(w=" AND ".join(model_conds)),
        vals)[0][0] or 0

    # Active prices
    # Need to join to items if category filter
    if category:
        active_prices = frappe.db.sql("""
            SELECT COUNT(*), COUNT(DISTINCT p.item_code), AVG(p.mrp), AVG(p.selling_price)
            FROM `tabCH Item Price` p
            JOIN `tabItem` i ON i.name = p.item_code
            WHERE {pw} AND {iw}
        """.format(pw=price_where, iw=item_where), vals)
    else:
        active_prices = frappe.db.sql("""
            SELECT COUNT(*), COUNT(DISTINCT p.item_code), AVG(p.mrp), AVG(p.selling_price)
            FROM `tabCH Item Price` p
            WHERE {pw}
        """.format(pw=price_where), vals)

    num_prices = active_prices[0][0] or 0
    priced_items = active_prices[0][1] or 0
    avg_mrp = flt(active_prices[0][2])
    avg_sp = flt(active_prices[0][3])

    # Offers
    if category:
        active_offers = frappe.db.sql("""
            SELECT COUNT(*)
            FROM `tabCH Item Offer` o
            JOIN `tabItem` i ON i.name = o.item_code
            WHERE {ow} AND {iw}
        """.format(ow=offer_where, iw=item_where), vals)[0][0] or 0
    else:
        active_offers = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabCH Item Offer` o
            WHERE {ow}
        """.format(ow=offer_where), vals)[0][0] or 0

    coverage = round(priced_items / total_items * 100, 1) if total_items > 0 else 0
    disc_pct = round((1 - avg_sp / avg_mrp) * 100, 1) if avg_mrp > 0 else 0

    # Health indicator
    if coverage >= 90:
        health = "🟢 Healthy"
    elif coverage >= 60:
        health = "🟡 Fair"
    else:
        health = "🔴 Low"

    return {
        "group_label": label,
        "total_models": total_models,
        "total_items": total_items,
        "total_templates": total_templates,
        "total_variants": total_items,
        "active_prices": num_prices,
        "priced_items": priced_items,
        "coverage_pct": coverage,
        "avg_mrp": avg_mrp,
        "avg_sp": avg_sp,
        "avg_discount_pct": disc_pct,
        "active_offers": active_offers,
        "health": health,
    }


def get_chart(data, group_by):
    if not data:
        return None

    labels = [r["group_label"] for r in data][:12]
    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": _("Items"), "values": [r["total_items"] for r in data][:12]},
                {"name": _("Priced"), "values": [r["priced_items"] for r in data][:12]},
                {"name": _("Offers"), "values": [r["active_offers"] for r in data][:12]},
            ],
        },
        "type": "bar",
        "colors": ["#3182ce", "#38a169", "#dd6b20"],
    }


def get_summary(data):
    total_items = sum(r["total_items"] for r in data)
    total_priced = sum(r["priced_items"] for r in data)
    total_offers = sum(r["active_offers"] for r in data)
    coverage = round(total_priced / total_items * 100, 1) if total_items > 0 else 0

    return [
        {"value": len(data), "indicator": "blue", "label": _("Groups"), "datatype": "Int"},
        {"value": total_items, "indicator": "blue", "label": _("Total Items"), "datatype": "Int"},
        {"value": total_priced, "indicator": "green", "label": _("Items Priced"), "datatype": "Int"},
        {"value": coverage, "indicator": "green" if coverage >= 80 else "orange",
         "label": _("Overall Coverage %"), "datatype": "Percent"},
        {"value": total_offers, "indicator": "orange", "label": _("Active Offers"), "datatype": "Int"},
    ]
