import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "scheme", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "model", "label": _("Model"), "fieldtype": "Link", "options": "CH Model", "width": 140},
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 100},
		{"fieldname": "avg_sale_price", "label": _("Avg Sale Price (₹)"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "current_rrp", "label": _("Current RRP (₹)"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "price_delta", "label": _("Price Drop (₹)"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "qty_sold", "label": _("Qty Sold"), "fieldtype": "Float", "width": 80},
		{"fieldname": "payout_at_risk", "label": _("Payout at Risk (₹)"), "fieldtype": "Currency", "width": 130},
	]


def get_data(filters):
	conditions = ["sal.is_reversed = 0"]
	values = {}

	if filters.get("scheme"):
		conditions.append("sal.scheme = %(scheme)s")
		values["scheme"] = filters["scheme"]
	if filters.get("brand"):
		conditions.append("sal.brand = %(brand)s")
		values["brand"] = filters["brand"]

	where = " AND ".join(conditions)

	rows = frappe.db.sql("""
		SELECT
			sal.scheme,
			sal.item_code,
			sal.model, sal.brand,
			AVG(sal.price_at_sale) as avg_sale_price,
			SUM(sal.qty) as qty_sold,
			SUM(sal.computed_payout) as payout_at_risk
		FROM `tabScheme Achievement Ledger` sal
		WHERE {where}
		GROUP BY sal.scheme, sal.item_code
		ORDER BY payout_at_risk DESC
	""".format(where=where), values, as_dict=True)  # noqa: UP032

	# Enrich with current RRP from Item Price
	for r in rows:
		current_price = frappe.db.get_value(
			"Item Price",
			{"item_code": r.item_code, "selling": 1},
			"price_list_rate",
		)
		r.current_rrp = flt(current_price)
		r.price_delta = flt(r.avg_sale_price) - flt(r.current_rrp)

	return rows
