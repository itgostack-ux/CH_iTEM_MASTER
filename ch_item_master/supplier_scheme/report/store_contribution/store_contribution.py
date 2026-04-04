import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "scheme", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "scheme_name", "label": _("Scheme Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "total_qty", "label": _("Total Qty"), "fieldtype": "Float", "width": 80},
		{"fieldname": "eligible_qty", "label": _("Eligible Qty"), "fieldtype": "Float", "width": 90},
		{"fieldname": "payout", "label": _("Payout (₹)"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "contribution_pct", "label": _("Contribution %"), "fieldtype": "Percent", "width": 110},
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
			sal.store,
			sal.scheme, sal.scheme_name,
			SUM(sal.qty) as total_qty,
			SUM(CASE WHEN sal.eligible_for_slab = 1 THEN sal.qty ELSE 0 END) as eligible_qty,
			SUM(CASE WHEN sal.eligible_for_payout = 1 THEN sal.computed_payout ELSE 0 END) as payout
		FROM `tabScheme Achievement Ledger` sal
		WHERE {where}
		GROUP BY sal.store, sal.scheme
		ORDER BY payout DESC
	""".format(where=where), values, as_dict=True)  # noqa: UP032

	grand_total = sum(r.payout or 0 for r in rows)
	for r in rows:
		r.contribution_pct = round((r.payout / grand_total * 100), 2) if grand_total else 0

	return rows
