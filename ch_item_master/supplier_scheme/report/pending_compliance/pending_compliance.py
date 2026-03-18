import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"fieldname": "name", "label": _("Entry"), "fieldtype": "Link", "options": "Scheme Achievement Ledger", "width": 140},
		{"fieldname": "scheme", "label": _("Scheme"), "fieldtype": "Link", "options": "Supplier Scheme Circular", "width": 140},
		{"fieldname": "invoice", "label": _("Invoice"), "fieldtype": "Data", "width": 140},
		{"fieldname": "invoice_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "serial_no", "label": _("Serial No"), "fieldtype": "Data", "width": 120},
		{"fieldname": "warranty_registered", "label": _("Warranty?"), "fieldtype": "Check", "width": 80},
		{"fieldname": "crm_updated", "label": _("CRM?"), "fieldtype": "Check", "width": 60},
		{"fieldname": "rejection_reason", "label": _("Pending Reason"), "fieldtype": "Data", "width": 200},
		{"fieldname": "potential_payout", "label": _("Potential (₹)"), "fieldtype": "Currency", "width": 110},
	]


def get_data(filters):
	conditions = [
		"sal.is_reversed = 0",
		"sal.eligible_for_payout = 0",
		"sal.demo_unit = 0",
	]
	values = {}

	if filters.get("scheme"):
		conditions.append("sal.scheme = %(scheme)s")
		values["scheme"] = filters["scheme"]
	if filters.get("store"):
		conditions.append("sal.store = %(store)s")
		values["store"] = filters["store"]

	where = " AND ".join(conditions)

	return frappe.db.sql(f"""
		SELECT
			sal.name, sal.scheme, sal.invoice, sal.invoice_date,
			sal.store, sal.item_code, sal.serial_no,
			sal.warranty_registered, sal.crm_updated,
			sal.rejection_reason,
			sal.qty * sal.price_at_sale * 0 as potential_payout
		FROM `tabScheme Achievement Ledger` sal
		WHERE {where}
		ORDER BY sal.invoice_date DESC
	""", values, as_dict=True)
