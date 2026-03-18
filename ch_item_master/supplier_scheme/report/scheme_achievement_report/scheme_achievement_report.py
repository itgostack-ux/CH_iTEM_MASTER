import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "scheme", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "scheme_name", "label": _("Scheme Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "rule_name", "label": _("Rule"), "fieldtype": "Data", "width": 140},
		{"fieldname": "invoice", "label": _("Invoice"), "fieldtype": "Data", "width": 140},
		{"fieldname": "invoice_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "serial_no", "label": _("Serial No"), "fieldtype": "Data", "width": 120},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 60},
		{"fieldname": "price_at_sale", "label": _("Sale Price"), "fieldtype": "Currency", "width": 100},
		{"fieldname": "eligible_for_slab", "label": _("Slab?"), "fieldtype": "Check", "width": 60},
		{"fieldname": "eligible_for_payout", "label": _("Payout?"), "fieldtype": "Check", "width": 60},
		{"fieldname": "computed_payout", "label": _("Payout (₹)"), "fieldtype": "Currency", "width": 100},
		{"fieldname": "rejection_reason", "label": _("Rejection"), "fieldtype": "Data", "width": 160},
		{"fieldname": "is_reversed", "label": _("Reversed"), "fieldtype": "Check", "width": 70},
	]


def get_data(filters):
	conditions = []
	values = {}

	if filters.get("scheme"):
		conditions.append("sal.scheme = %(scheme)s")
		values["scheme"] = filters["scheme"]
	if filters.get("brand"):
		conditions.append("sal.brand = %(brand)s")
		values["brand"] = filters["brand"]
	if filters.get("store"):
		conditions.append("sal.store = %(store)s")
		values["store"] = filters["store"]
	if filters.get("from_date"):
		conditions.append("sal.invoice_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("sal.invoice_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]
	if not filters.get("show_reversed"):
		conditions.append("sal.is_reversed = 0")

	where = " AND ".join(conditions) if conditions else "1=1"

	return frappe.db.sql(f"""
		SELECT
			sal.scheme, sal.scheme_name, sal.rule_name,
			sal.invoice, sal.invoice_date, sal.store,
			sal.item_code, sal.serial_no, sal.qty,
			sal.price_at_sale, sal.eligible_for_slab,
			sal.eligible_for_payout, sal.computed_payout,
			sal.rejection_reason, sal.is_reversed
		FROM `tabScheme Achievement Ledger` sal
		WHERE {where}
		ORDER BY sal.invoice_date DESC, sal.scheme
	""", values, as_dict=True)
