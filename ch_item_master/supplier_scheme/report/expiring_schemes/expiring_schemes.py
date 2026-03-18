import frappe
from frappe import _
from frappe.utils import date_diff, getdate, nowdate


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "name", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "scheme_name", "label": _("Scheme Name"), "fieldtype": "Data", "width": 200},
		{"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 100},
		{"fieldname": "valid_to", "label": _("Expires On"), "fieldtype": "Date", "width": 110},
		{"fieldname": "days_remaining", "label": _("Days Left"), "fieldtype": "Int", "width": 80},
		{"fieldname": "total_eligible_qty", "label": _("Eligible Qty"), "fieldtype": "Float", "width": 90},
		{"fieldname": "total_claim_amount", "label": _("Claim (₹)"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 80},
	]


def get_data(filters):
	threshold = filters.get("days_threshold", 15)
	today = getdate(nowdate())

	schemes = frappe.get_all(
		"Supplier Scheme Circular",
		filters={
			"docstatus": 1,
			"status": "Active",
		},
		fields=["name", "scheme_name", "brand", "valid_to",
				"total_eligible_qty", "total_claim_amount", "status"],
		order_by="valid_to ASC",
	)

	data = []
	for s in schemes:
		days = date_diff(getdate(s.valid_to), today)
		if days <= threshold:
			s["days_remaining"] = max(days, 0)
			data.append(s)

	return data
