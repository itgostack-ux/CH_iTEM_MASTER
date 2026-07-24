import frappe
from frappe import _
from frappe.utils import flt

from ch_item_master.config import get_int_setting, get_setting, require_role_setting
from ch_item_master.security import get_company_scope


def execute(filters=None):
    require_role_setting(
        "price_view_roles",
        ("CH Price Manager", "CH Master Manager", "CH Viewer"),
        action=_("view supplier price comparison"),
    )
    for doctype in ("Purchase Invoice", "Item", "Supplier", "CH Item Price"):
        frappe.has_permission(doctype, "read", throw=True)
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
    price_conditions = [
        "p.status = 'Active'",
        "p.channel = %(channel)s",
        "(p.effective_from IS NULL OR p.effective_from <= CURRENT_DATE)",
        "(p.effective_to IS NULL OR p.effective_to >= CURRENT_DATE)",
    ]
    values = {
        "channel": filters.get("channel") or get_setting("default_sales_price_channel", "POS"),
    }

    company_scope = get_company_scope(requested_company=filters.get("company") or None)
    if not filters.get("company") and company_scope and len(company_scope) > 1:
        frappe.throw(_("Select a company to compare supplier prices."), frappe.ValidationError)
    if company_scope is not None:
        values["companies"] = tuple(company_scope or ["__no_company_scope__"])
        conditions.append("pi.company IN %(companies)s")
        price_conditions.append("p.company IN %(companies)s")

    if filters.get("supplier"):
        conditions.append("pi.supplier = %(supplier)s")
        values["supplier"] = filters.supplier

    if filters.get("item_code"):
        conditions.append("pii.item_code = %(item_code)s")
        values["item_code"] = filters.item_code

    row_limit = min(get_int_setting("interactive_report_row_limit", 2000, minimum=1), 10000)
    values["result_limit"] = row_limit + 1
    records = frappe.db.sql(
        """
        WITH ranked_purchases AS (
            SELECT
                pii.item_code,
                pii.item_name,
                pi.supplier,
                pi.supplier_name,
                pi.posting_date,
                pii.rate,
                AVG(pii.rate) OVER (
                    PARTITION BY pii.item_code, pi.supplier
                ) AS avg_purchase_rate,
                ROW_NUMBER() OVER (
                    PARTITION BY pii.item_code, pi.supplier
                    ORDER BY pi.posting_date DESC, pi.modified DESC, pi.name DESC, pii.idx DESC
                ) AS purchase_rank
            FROM `tabPurchase Invoice Item` pii
            INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            WHERE {purchase_conditions}
        ),
        ranked_prices AS (
            SELECT
                p.item_code,
                p.selling_price,
                p.channel,
                ROW_NUMBER() OVER (
                    PARTITION BY p.item_code
                    ORDER BY p.effective_from DESC, p.modified DESC, p.name DESC
                ) AS price_rank
            FROM `tabCH Item Price` p
            WHERE {price_conditions}
        )
        SELECT
            purchase.item_code,
            purchase.item_name,
            purchase.supplier,
            purchase.supplier_name,
            purchase.posting_date AS last_purchase_date,
            purchase.rate AS last_purchase_rate,
            purchase.avg_purchase_rate,
            price.selling_price,
            COALESCE(price.channel, %(channel)s) AS channel
        FROM ranked_purchases purchase
        LEFT JOIN ranked_prices price
          ON price.item_code = purchase.item_code AND price.price_rank = 1
        WHERE purchase.purchase_rank = 1
        ORDER BY purchase.item_code ASC, purchase.supplier ASC
        LIMIT %(result_limit)s
        """.format(
            purchase_conditions=" AND ".join(conditions),
            price_conditions=" AND ".join(price_conditions),
        ),
        values,
        as_dict=True,
    )
    if len(records) > row_limit:
        frappe.throw(
            _("Supplier Price Comparison exceeds the configured limit of {0} rows. Narrow the filters.").format(
                row_limit
            ),
            frappe.ValidationError,
        )

    data = []
    for row in records:
        row["last_purchase_rate"] = flt(row.last_purchase_rate)
        row["avg_purchase_rate"] = round(flt(row.avg_purchase_rate), 2)
        row["selling_price"] = flt(row.selling_price)
        if row["selling_price"] and row["last_purchase_rate"]:
            row["gross_margin_percent"] = round(((row["selling_price"] - row["last_purchase_rate"]) / row["selling_price"]) * 100, 2)
        else:
            row["gross_margin_percent"] = 0
        data.append(row)
    return data
