import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "name", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "scheme_name", "label": _("Scheme Name"), "fieldtype": "Data", "width": 200},
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 100},
		{"fieldname": "valid_to", "label": _("Ended On"), "fieldtype": "Date", "width": 110},
		{"fieldname": "total_claim_amount", "label": _("Unclaimed (₹)"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 80},
		{"fieldname": "days_since_expiry", "label": _("Days Since Expiry"), "fieldtype": "Int", "width": 120},
	]


def get_data(filters):
	"""Schemes that are Closed/expired but have no claim raised."""
	today = getdate(nowdate())

	schemes = frappe.db.sql("""
		SELECT
			ssc.name, ssc.scheme_name, ssc.brand, ssc.valid_to,
			ssc.total_claim_amount, ssc.status
		FROM `tabSupplier Scheme Circular` ssc
		WHERE ssc.docstatus = 1
		  AND ssc.status IN ('Closed', 'Active')
		  AND ssc.valid_to < %(today)s
		  AND ssc.total_claim_amount > 0
		  AND NOT EXISTS (
			SELECT 1 FROM `tabScheme Claim Summary` cl
			WHERE cl.scheme = ssc.name AND cl.docstatus != 2
		  )
		ORDER BY ssc.total_claim_amount DESC
	""", {"today": today}, as_dict=True)

	from frappe.utils import date_diff
	for s in schemes:
		s["days_since_expiry"] = date_diff(today, getdate(s.valid_to))

	return schemes
