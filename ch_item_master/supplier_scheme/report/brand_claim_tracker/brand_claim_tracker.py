import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 120},
		{"fieldname": "scheme", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "scheme_name", "label": _("Scheme Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "valid_from", "label": _("From"), "fieldtype": "Date", "width": 100},
		{"fieldname": "valid_to", "label": _("To"), "fieldtype": "Date", "width": 100},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "total_qty", "label": _("Total Qty"), "fieldtype": "Float", "width": 80},
		{"fieldname": "eligible_qty", "label": _("Eligible Qty"), "fieldtype": "Float", "width": 90},
		{"fieldname": "total_claim", "label": _("Claim (₹)"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "settled", "label": _("Settled (₹)"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "pending", "label": _("Pending (₹)"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "claim_status", "label": _("Claim Status"), "fieldtype": "Data", "width": 110},
	]


def get_data(filters):
	conditions = ["ssc.docstatus = 1"]
	values = {}

	if filters.get("brand"):
		conditions.append("ssc.brand = %(brand)s")
		values["brand"] = filters["brand"]
	if filters.get("status"):
		conditions.append("ssc.status = %(status)s")
		values["status"] = filters["status"]

	where = " AND ".join(conditions)

	return frappe.db.sql("""
		SELECT
			ssc.brand, ssc.name as scheme, ssc.scheme_name,
			ssc.valid_from, ssc.valid_to, ssc.status,
			ssc.total_eligible_qty as total_qty,
			ssc.total_eligible_qty as eligible_qty,
			ssc.total_claim_amount as total_claim,
			ssc.total_settled_amount as settled,
			ssc.total_pending_amount as pending,
			IFNULL(cl.claim_status, 'No Claim') as claim_status
		FROM `tabSupplier Scheme Circular` ssc
		LEFT JOIN `tabScheme Claim Summary` cl
			ON cl.scheme = ssc.name AND cl.docstatus != 2
		WHERE {where}
		ORDER BY ssc.brand, ssc.valid_from DESC
	""".format(where=where), values, as_dict=True)  # noqa: UP032
