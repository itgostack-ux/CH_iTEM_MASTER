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

from ch_item_master.config import get_int_setting, require_role_setting
from ch_item_master.security import get_company_scope


def execute(filters=None):
    require_role_setting(
        "app_access_roles",
        ("CH Master Manager", "CH Price Manager", "CH Viewer"),
        action=_("view the category manager report"),
    )
    for doctype in ("CH Model", "Item", "CH Item Price", "CH Item Offer"):
        frappe.has_permission(doctype, "read", throw=True)
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
    conditions = ["m.disabled = 0"]
    values = {"current_date": today()}

    if filters.get("category"):
        conditions.append("sc.category = %(category)s")
        values["category"] = filters["category"]

    if filters.get("sub_category"):
        conditions.append("m.sub_category = %(sub_category)s")
        values["sub_category"] = filters["sub_category"]

    where = " AND ".join(conditions)

    row_limit = min(get_int_setting("interactive_report_row_limit", 2000, minimum=1), 10000)
    values["row_limit"] = row_limit + 1
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
        LIMIT %(row_limit)s
    """.format(where=where), values, as_dict=True)  # noqa: UP032

    if len(models) > row_limit:
        frappe.throw(
            _("Category Manager Report exceeds the configured limit of {0} models. Narrow the filters.").format(
                row_limit
            ),
            frappe.ValidationError,
        )
    if not models:
        return []

    model_names = tuple(row.model for row in models)

    # Price/offer filter conditions
    price_conds = [
        "p.status = 'Active'",
        "(p.effective_from IS NULL OR p.effective_from <= %(current_date)s)",
        "(p.effective_to IS NULL OR p.effective_to >= %(current_date)s)",
    ]
    offer_conds = [
        "o.status = 'Active'",
        "(o.start_date IS NULL OR o.start_date <= %(current_date)s)",
        "(o.end_date IS NULL OR o.end_date >= %(current_date)s)",
    ]
    company_scope = get_company_scope(requested_company=filters.get("company") or None)
    if company_scope is not None:
        values["companies"] = tuple(company_scope or ["__no_company_scope__"])
        price_conds.append("p.company IN %(companies)s")
        offer_conds.append("o.company IN %(companies)s")
    if filters.get("channel"):
        price_conds.append("p.channel = %(channel)s")
        offer_conds.append("(o.channel = %(channel)s OR o.channel IS NULL OR o.channel = '')")
        values["channel"] = filters["channel"]

    price_where = " AND ".join(price_conds)
    offer_where = " AND ".join(offer_conds)

    item_counts = frappe.db.sql(
        """
        SELECT ch_model AS model, COUNT(*) AS total_items
        FROM `tabItem`
        WHERE ch_model IN %(models)s AND has_variants = 0 AND disabled = 0
        GROUP BY ch_model
        """,
        {"models": model_names},
        as_dict=True,
    )
    item_counts_by_model = {row.model: int(row.total_items or 0) for row in item_counts}

    price_data = frappe.db.sql(
        """
        SELECT
            i.ch_model AS model,
            COUNT(DISTINCT p.item_code) AS priced_items,
            AVG(p.mrp) AS avg_mrp,
            AVG(p.selling_price) AS avg_sp
        FROM `tabItem` i
        INNER JOIN `tabCH Item Price` p ON p.item_code = i.name
        WHERE i.ch_model IN %(models)s
          AND i.has_variants = 0
          AND i.disabled = 0
          AND {conditions}
        GROUP BY i.ch_model
        """.format(conditions=price_where),
        {**values, "models": model_names},
        as_dict=True,
    )
    prices_by_model = {row.model: row for row in price_data}

    offer_data = frappe.db.sql(
        """
        SELECT i.ch_model AS model, COUNT(*) AS active_offers
        FROM `tabItem` i
        INNER JOIN `tabCH Item Offer` o ON o.item_code = i.name
        WHERE i.ch_model IN %(models)s
          AND i.has_variants = 0
          AND i.disabled = 0
          AND {conditions}
        GROUP BY i.ch_model
        """.format(conditions=offer_where),
        {**values, "models": model_names},
        as_dict=True,
    )
    offers_by_model = {row.model: int(row.active_offers or 0) for row in offer_data}

    data = []
    for row in models:
        total_items = item_counts_by_model.get(row.model, 0)
        price_row = prices_by_model.get(row.model, {})
        priced_items = int(price_row.get("priced_items") or 0)
        avg_mrp = price_row.get("avg_mrp") or 0
        avg_sp = price_row.get("avg_sp") or 0
        active_offers = offers_by_model.get(row.model, 0)

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
