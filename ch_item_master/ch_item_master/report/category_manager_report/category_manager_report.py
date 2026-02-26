# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Category Manager Report — Script Report

Deep-dive view for category managers showing:
- Sub-category → Model → Item hierarchy with counts
- Pricing coverage per sub-category (items with active price vs total)
- Active offers count
- Average selling price & MRP
- Gap indicators (models without items, items without prices)
"""

import frappe
from frappe import _
from frappe.utils import today


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"fieldname": "sub_category", "label": _("Sub Category"), "fieldtype": "Link",
         "options": "CH Sub Category", "width": 160},
        {"fieldname": "model", "label": _("Model"), "fieldtype": "Link",
         "options": "CH Model", "width": 140},
        {"fieldname": "model_name", "label": _("Model Name"), "fieldtype": "Data", "width": 180},
        {"fieldname": "manufacturer", "label": _("Manufacturer"), "fieldtype": "Link",
         "options": "Manufacturer", "width": 120},
        {"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link",
         "options": "Brand", "width": 100},
        {"fieldname": "total_items", "label": _("Items"), "fieldtype": "Int", "width": 70},
        {"fieldname": "priced_items", "label": _("Priced"), "fieldtype": "Int", "width": 70},
        {"fieldname": "unpriced_items", "label": _("Unpriced"), "fieldtype": "Int", "width": 80},
        {"fieldname": "coverage_pct", "label": _("Price Coverage %"), "fieldtype": "Percent", "width": 120},
        {"fieldname": "avg_mrp", "label": _("Avg MRP"), "fieldtype": "Currency", "width": 100},
        {"fieldname": "avg_sp", "label": _("Avg SP"), "fieldtype": "Currency", "width": 100},
        {"fieldname": "active_offers", "label": _("Offers"), "fieldtype": "Int", "width": 70},
        {"fieldname": "status_flag", "label": _("Status"), "fieldtype": "Data", "width": 100},
    ]


def get_data(filters):
    conditions = ["m.is_active = 1"]
    values = {}
    cur_date = today()

    if filters.get("category"):
        conditions.append("sc.category = %(category)s")
        values["category"] = filters["category"]

    if filters.get("sub_category"):
        conditions.append("m.sub_category = %(sub_category)s")
        values["sub_category"] = filters["sub_category"]

    where = " AND ".join(conditions)

    models = frappe.db.sql("""
        SELECT
            m.name AS model,
            m.model_name,
            m.sub_category,
            m.manufacturer,
            m.brand
        FROM `tabCH Model` m
        JOIN `tabCH Sub Category` sc ON sc.name = m.sub_category
        WHERE {where}
        ORDER BY sc.category, m.sub_category, m.model_name
    """.format(where=where), values, as_dict=True)

    # Price/offer filter conditions
    price_conds = ["p.status = 'Active'"]
    offer_conds = ["o.status = 'Active'"]
    if filters.get("company"):
        price_conds.append("p.company = %(company)s")
        offer_conds.append("o.company = %(company)s")
        values["company"] = filters["company"]
    if filters.get("channel"):
        price_conds.append("p.channel = %(channel)s")
        offer_conds.append("(o.channel = %(channel)s OR o.channel IS NULL OR o.channel = '')")
        values["channel"] = filters["channel"]

    price_where = " AND ".join(price_conds)
    offer_where = " AND ".join(offer_conds)

    data = []
    for row in models:
        # Count items for this model
        total_items = frappe.db.count("Item", {
            "ch_model": row.model,
            "has_variants": 0,
            "disabled": 0,
        })

        if total_items == 0:
            # Check if template exists
            template_exists = frappe.db.count("Item", {
                "ch_model": row.model,
                "has_variants": 1,
            })
            total_items = 0

        # Count priced items
        priced_items = 0
        avg_mrp = 0
        avg_sp = 0
        if total_items > 0:
            item_codes = frappe.db.get_all("Item", {
                "ch_model": row.model,
                "has_variants": 0,
                "disabled": 0,
            }, pluck="name")

            if item_codes:
                values["_items"] = item_codes
                price_data = frappe.db.sql("""
                    SELECT
                        COUNT(DISTINCT p.item_code) as priced,
                        AVG(p.mrp) as avg_mrp,
                        AVG(p.selling_price) as avg_sp
                    FROM `tabCH Item Price` p
                    WHERE p.item_code IN %(items)s AND {cond}
                """.format(cond=price_where), {**values, "items": item_codes}, as_dict=True)

                if price_data:
                    priced_items = price_data[0].priced or 0
                    avg_mrp = price_data[0].avg_mrp or 0
                    avg_sp = price_data[0].avg_sp or 0

        # Count offers
        active_offers = 0
        if total_items > 0:
            item_codes = frappe.db.get_all("Item", {
                "ch_model": row.model,
                "has_variants": 0,
                "disabled": 0,
            }, pluck="name")
            if item_codes:
                active_offers = frappe.db.sql("""
                    SELECT COUNT(*) FROM `tabCH Item Offer` o
                    WHERE o.item_code IN %(items)s AND {cond}
                """.format(cond=offer_where), {**values, "items": item_codes})[0][0] or 0

        unpriced = total_items - priced_items
        coverage = round((priced_items / total_items * 100), 1) if total_items > 0 else 0

        # Status flag
        if total_items == 0:
            status_flag = "⚠ No Items"
        elif unpriced > 0:
            status_flag = "⚠ Gap: {} unpriced".format(unpriced)
        else:
            status_flag = "✅ Complete"

        data.append({
            "sub_category": row.sub_category,
            "model": row.model,
            "model_name": row.model_name,
            "manufacturer": row.manufacturer,
            "brand": row.brand,
            "total_items": total_items,
            "priced_items": priced_items,
            "unpriced_items": unpriced,
            "coverage_pct": coverage,
            "avg_mrp": avg_mrp,
            "avg_sp": avg_sp,
            "active_offers": active_offers,
            "status_flag": status_flag,
        })

    return data


def get_chart(data):
    if not data:
        return None

    # Aggregate by sub-category
    sc_data = {}
    for row in data:
        sc = row["sub_category"]
        if sc not in sc_data:
            sc_data[sc] = {"total": 0, "priced": 0, "unpriced": 0}
        sc_data[sc]["total"] += row["total_items"]
        sc_data[sc]["priced"] += row["priced_items"]
        sc_data[sc]["unpriced"] += row["unpriced_items"]

    labels = list(sc_data.keys())[:15]
    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": _("Priced Items"), "values": [sc_data[l]["priced"] for l in labels]},
                {"name": _("Unpriced Items"), "values": [sc_data[l]["unpriced"] for l in labels]},
            ],
        },
        "type": "bar",
        "colors": ["#38a169", "#e53e3e"],
        "barOptions": {"stacked": True},
    }


def get_summary(data):
    total_models = len(data)
    total_items = sum(r["total_items"] for r in data)
    priced_items = sum(r["priced_items"] for r in data)
    unpriced = total_items - priced_items
    coverage = round((priced_items / total_items * 100), 1) if total_items > 0 else 0
    models_no_items = sum(1 for r in data if r["total_items"] == 0)

    return [
        {"value": total_models, "indicator": "blue", "label": _("Models"), "datatype": "Int"},
        {"value": total_items, "indicator": "blue", "label": _("Total Items"), "datatype": "Int"},
        {"value": priced_items, "indicator": "green", "label": _("Priced Items"), "datatype": "Int"},
        {"value": unpriced, "indicator": "red" if unpriced > 0 else "green", "label": _("Unpriced"), "datatype": "Int"},
        {"value": coverage, "indicator": "green" if coverage >= 80 else "orange", "label": _("Coverage %"), "datatype": "Percent"},
        {"value": models_no_items, "indicator": "red" if models_no_items > 0 else "green", "label": _("Models w/o Items"), "datatype": "Int"},
    ]
