import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "scheme", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "scheme_name", "label": _("Scheme Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 100},
		{"fieldname": "claim", "label": _("Claim"), "fieldtype": "Link", "options": "Scheme Claim Summary", "width": 140},
		{"fieldname": "total_payout", "label": _("Total Payout (₹)"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "tds_percent", "label": _("TDS %"), "fieldtype": "Percent", "width": 80},
		{"fieldname": "tds_amount", "label": _("TDS Amount (₹)"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "net_claim", "label": _("Net Claim (₹)"), "fieldtype": "Currency", "width": 120},
	]


def get_data(filters):
	conditions = ["cl.docstatus != 2", "cl.tds_amount > 0"]
	values = {}

	if filters.get("brand"):
		conditions.append("cl.brand = %(brand)s")
		values["brand"] = filters["brand"]

	where = " AND ".join(conditions)

	return frappe.db.sql("""
		SELECT
			cl.scheme, cl.scheme_name, cl.brand,
			cl.name as claim,
			cl.total_payout, cl.tds_percent,
			cl.tds_amount, cl.net_claim
		FROM `tabScheme Claim Summary` cl
		WHERE {where}
		ORDER BY cl.brand, cl.tds_amount DESC
	""".format(where=where), values, as_dict=True)  # noqa: UP032
