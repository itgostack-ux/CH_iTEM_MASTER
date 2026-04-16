import frappe
from frappe import _
from frappe.utils import flt



def execute(filters=None):
    filters = frappe._dict(filters or {})
    return get_columns(), get_data(filters)



def get_columns():
    return [
        {"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
        {"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 160},
        {"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 170},
        {"label": _("Last Purchase Date"), "fieldname": "last_purchase_date", "fieldtype": "Date", "width": 120},
        {"label": _("Last Purchase Rate"), "fieldname": "last_purchase_rate", "fieldtype": "Currency", "width": 125},
        {"label": _("Avg Purchase Rate"), "fieldname": "avg_purchase_rate", "fieldtype": "Currency", "width": 125},
        {"label": _("Active Sell Price"), "fieldname": "selling_price", "fieldtype": "Currency", "width": 120},
        {"label": _("Channel"), "fieldname": "channel", "fieldtype": "Data", "width": 110},
        {"label": _("Gross Margin %"), "fieldname": "gross_margin_percent", "fieldtype": "Percent", "width": 110},
    ]



def get_data(filters):
    conditions = ["pi.docstatus = 1"]
    values = {}

    if filters.get("company"):
        conditions.append("pi.company = %(company)s")
        values["company"] = filters.company

    if filters.get("supplier"):
        conditions.append("pi.supplier = %(supplier)s")
        values["supplier"] = filters.supplier

    if filters.get("item_code"):
        conditions.append("pii.item_code = %(item_code)s")
        values["item_code"] = filters.item_code

    records = frappe.db.sql(
        f"""
        SELECT
            pii.item_code,
            pii.item_name,
            pi.supplier,
            pi.supplier_name,
            pi.posting_date,
            pii.rate
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        WHERE {' AND '.join(conditions)}
        ORDER BY pii.item_code ASC, pi.supplier ASC, pi.posting_date DESC, pi.modified DESC
        """,
        values,
        as_dict=True,
    )

    sell_channel = filters.get("channel") or "POS"
    active_prices = {
        (r.item_code): r for r in frappe.get_all(
            "CH Item Price",
            filters={"status": "Active", "channel": sell_channel},
            fields=["item_code", "selling_price", "channel"],
            order_by="effective_from desc",
        )
    }

    grouped = {}
    for rec in records:
        key = (rec.item_code, rec.supplier)
        row = grouped.setdefault(key, {
            "item_code": rec.item_code,
            "item_name": rec.item_name,
            "supplier": rec.supplier,
            "supplier_name": rec.supplier_name,
            "last_purchase_date": rec.posting_date,
            "last_purchase_rate": flt(rec.rate),
            "rates": [],
        })
        row["rates"].append(flt(rec.rate))

    data = []
    for row in grouped.values():
        row["avg_purchase_rate"] = round(sum(row["rates"]) / len(row["rates"]), 2) if row["rates"] else 0
        price_row = active_prices.get(row["item_code"])
        row["selling_price"] = flt(price_row.selling_price) if price_row else 0
        row["channel"] = price_row.channel if price_row else sell_channel
        if row["selling_price"] and row["last_purchase_rate"]:
            row["gross_margin_percent"] = round(((row["selling_price"] - row["last_purchase_rate"]) / row["selling_price"]) * 100, 2)
        else:
            row["gross_margin_percent"] = 0
        row.pop("rates", None)
        data.append(row)

    data.sort(key=lambda d: (d.get("item_code") or "", d.get("supplier") or ""))
    return data
