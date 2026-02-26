# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Expiring Prices — Script Report

Shows Active prices that will expire within the next N days (default 7).
Helps the pricing team proactively renew or replace expiring prices
before items become un-priced.
"""

import frappe
from frappe import _
from frappe.utils import add_days, nowdate, getdate


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "name", "label": _("Price ID"), "fieldtype": "Link",
         "options": "CH Item Price", "width": 150},
        {"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link",
         "options": "Item", "width": 130},
        {"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
        {"fieldname": "channel", "label": _("Channel"), "fieldtype": "Link",
         "options": "CH Price Channel", "width": 120},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link",
         "options": "Company", "width": 140},
        {"fieldname": "mrp", "label": _("MRP"), "fieldtype": "Currency", "width": 100},
        {"fieldname": "selling_price", "label": _("Selling Price"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "effective_to", "label": _("Expires On"), "fieldtype": "Date", "width": 110},
        {"fieldname": "days_left", "label": _("Days Left"), "fieldtype": "Int", "width": 90},
        {"fieldname": "has_replacement", "label": _("Replacement?"), "fieldtype": "Data", "width": 110},
    ]


def get_data(filters):
    today = nowdate()
    days = int(filters.get("days_ahead") or 7)
    cutoff = add_days(today, days)

    conditions = [
        "p.status = 'Active'",
        "p.effective_to IS NOT NULL",
        "p.effective_to >= %(today)s",
        "p.effective_to <= %(cutoff)s",
    ]
    values = {"today": today, "cutoff": cutoff}

    if filters.get("channel"):
        conditions.append("p.channel = %(channel)s")
        values["channel"] = filters["channel"]

    if filters.get("company"):
        conditions.append("p.company = %(company)s")
        values["company"] = filters["company"]

    rows = frappe.db.sql("""
        SELECT
            p.name,
            p.item_code,
            p.item_name,
            p.channel,
            p.company,
            p.mrp,
            p.selling_price,
            p.effective_to,
            DATEDIFF(p.effective_to, %(today)s) as days_left
        FROM `tabCH Item Price` p
        WHERE {conditions}
        ORDER BY p.effective_to ASC, p.item_code
    """.format(conditions=" AND ".join(conditions)), values, as_dict=True)

    # Check for replacement (a Scheduled price for same item+channel)
    for row in rows:
        replacement = frappe.db.get_value(
            "CH Item Price",
            {
                "item_code": row.item_code,
                "channel": row.channel,
                "status": "Scheduled",
                "name": ("!=", row.name),
            },
            "name",
        )
        row["has_replacement"] = "Yes" if replacement else "No"

    return rows
