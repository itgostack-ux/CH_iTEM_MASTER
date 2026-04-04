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
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 100},
		{"fieldname": "claim", "label": _("Claim"), "fieldtype": "Link", "options": "Scheme Claim Summary", "width": 140},
		{"fieldname": "claim_status", "label": _("Claim Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "net_claim", "label": _("Net Claim (₹)"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "total_settled", "label": _("Received (₹)"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "pending", "label": _("Pending (₹)"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "recovery_pct", "label": _("Recovery %"), "fieldtype": "Percent", "width": 100},
	]


def get_data(filters):
	conditions = ["cl.docstatus = 1"]
	values = {}

	if filters.get("brand"):
		conditions.append("cl.brand = %(brand)s")
		values["brand"] = filters["brand"]
	if filters.get("claim_status"):
		conditions.append("cl.claim_status = %(claim_status)s")
		values["claim_status"] = filters["claim_status"]

	where = " AND ".join(conditions)

	rows = frappe.db.sql("""
		SELECT
			cl.scheme, cl.scheme_name, cl.brand,
			cl.name as claim, cl.claim_status,
			cl.net_claim,
			IFNULL((
				SELECT SUM(s.received_amount)
				FROM `tabScheme Settlement` s
				WHERE s.claim_summary = cl.name AND s.docstatus = 1
			), 0) as total_settled
		FROM `tabScheme Claim Summary` cl
		WHERE {where}
		ORDER BY cl.brand, cl.scheme
	""".format(where=where), values, as_dict=True)  # noqa: UP032

	for r in rows:
		r.pending = flt(r.net_claim) - flt(r.total_settled)
		r.recovery_pct = round(flt(r.total_settled) / flt(r.net_claim) * 100, 2) if flt(r.net_claim) > 0 else 0

	return rows
