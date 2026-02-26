# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Items Without Active Price — Script Report

Shows all CH-managed items (variant items with ch_model set) that have NO
Active price record in any channel. Helps ensure every sellable item has pricing.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link",
         "options": "Item", "width": 150},
        {"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 250},
        {"fieldname": "ch_model", "label": _("Model"), "fieldtype": "Link",
         "options": "CH Model", "width": 150},
        {"fieldname": "ch_sub_category", "label": _("Sub Category"), "fieldtype": "Link",
         "options": "CH Sub Category", "width": 150},
        {"fieldname": "ch_category", "label": _("Category"), "fieldtype": "Link",
         "options": "CH Category", "width": 120},
        {"fieldname": "last_expired", "label": _("Last Expired Price"), "fieldtype": "Date", "width": 130},
        {"fieldname": "total_prices", "label": _("Total Prices (all time)"), "fieldtype": "Int", "width": 130},
    ]


def get_data(filters):
    conditions = ["i.ch_model IS NOT NULL", "i.ch_model != ''",
                   "i.has_variants = 0", "i.disabled = 0"]
    values = {}

    if filters.get("category"):
        conditions.append("i.ch_category = %(category)s")
        values["category"] = filters["category"]

    if filters.get("sub_category"):
        conditions.append("i.ch_sub_category = %(sub_category)s")
        values["sub_category"] = filters["sub_category"]

    if filters.get("model"):
        conditions.append("i.ch_model = %(model)s")
        values["model"] = filters["model"]

    return frappe.db.sql("""
        SELECT
            i.item_code,
            i.item_name,
            i.ch_model,
            i.ch_sub_category,
            i.ch_category,
            (
                SELECT MAX(p.effective_to)
                FROM `tabCH Item Price` p
                WHERE p.item_code = i.item_code AND p.status = 'Expired'
            ) AS last_expired,
            (
                SELECT COUNT(*)
                FROM `tabCH Item Price` p2
                WHERE p2.item_code = i.item_code
            ) AS total_prices
        FROM `tabItem` i
        WHERE {conditions}
          AND NOT EXISTS (
            SELECT 1 FROM `tabCH Item Price` ap
            WHERE ap.item_code = i.item_code AND ap.status = 'Active'
          )
        ORDER BY i.ch_category, i.ch_sub_category, i.item_name
    """.format(conditions=" AND ".join(conditions)), values, as_dict=True)
