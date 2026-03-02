# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Transaction hooks for CH Customer Master.
Auto-logs store visits and updates activity summary on Customer.
Hooked via ch_item_master hooks.py doc_events.
"""

import frappe
from frappe.utils import cint, flt, today


def on_sales_invoice_submit(doc, method=None):
	"""When a Sales Invoice is submitted:
	1. Log a store visit (Purchase type)
	2. Update activity summary on Customer
	3. Update ch_customer_since if not set
	"""
	if not doc.customer:
		return

	_log_store_visit(
		customer=doc.customer,
		company=doc.company,
		visit_type="Purchase",
		reference_doctype="Sales Invoice",
		reference_name=doc.name,
		store=doc.get("set_warehouse"),
		staff=doc.owner,
	)
	_update_activity_summary(doc.customer)


def on_service_request_submit(doc, method=None):
	"""When a Service Request changes status, log a visit."""
	if not doc.customer:
		return

	_log_store_visit(
		customer=doc.customer,
		company=doc.company,
		visit_type="Service",
		reference_doctype="Service Request",
		reference_name=doc.name,
		store=doc.get("warehouse_address"),
		staff=doc.owner,
	)
	_update_activity_summary(doc.customer)


def on_buyback_request_submit(doc, method=None):
	"""When a Buyback Request is created, log a visit."""
	customer = _find_customer_by_mobile(doc.get("mobile_no"))
	if not customer:
		return

	_log_store_visit(
		customer=customer,
		company=frappe.defaults.get_defaults().get("company", ""),
		visit_type="Buyback",
		reference_doctype="Buyback Request",
		reference_name=doc.name,
		staff=doc.owner,
	)
	_update_activity_summary(customer)


def _log_store_visit(customer, company, visit_type, reference_doctype=None,
					 reference_name=None, store=None, staff=None):
	"""Create a CH Customer Store Visit entry in the Customer's child table."""
	try:
		cust_doc = frappe.get_doc("Customer", customer)
		cust_doc.append("ch_stores_visited", {
			"visit_date": today(),
			"store": store,
			"company": company,
			"visit_type": visit_type,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"staff": staff,
		})

		# Update last visit info
		cust_doc.ch_last_visit_date = today()
		if store:
			cust_doc.ch_last_visit_store = frappe.db.get_value("Warehouse", store, "warehouse_name") or store

		cust_doc.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"CH Customer Store Visit Error: {customer}",
			message=frappe.get_traceback(),
		)


def _update_activity_summary(customer):
	"""Recalculate and update activity summary fields on Customer."""
	try:
		# Total purchases (sum of Sales Invoice grand_total)
		total_purchases = frappe.db.sql(
			"""SELECT IFNULL(SUM(grand_total), 0)
			FROM `tabSales Invoice`
			WHERE customer = %s AND docstatus = 1""",
			customer,
		)[0][0]

		# Total service requests
		total_services = frappe.db.count(
			"Service Request", {"customer": customer}
		) if frappe.db.exists("DocType", "Service Request") else 0

		# Total buybacks (by mobile match)
		total_buybacks = 0
		mobile = frappe.db.get_value("Customer", customer, "mobile_no")
		if mobile and frappe.db.exists("DocType", "Buyback Request"):
			total_buybacks = frappe.db.count(
				"Buyback Request", {"mobile_no": mobile[-10:]}
			)

		# Active devices
		active_devices = 0
		if frappe.db.exists("DocType", "CH Customer Device"):
			active_devices = frappe.db.count(
				"CH Customer Device",
				{"customer": customer, "current_status": "Owned"},
			)

		# Loyalty balance
		loyalty_balance = 0
		if frappe.db.exists("DocType", "CH Loyalty Transaction"):
			result = frappe.db.sql(
				"""SELECT IFNULL(SUM(points), 0)
				FROM `tabCH Loyalty Transaction`
				WHERE customer = %s AND docstatus = 1 AND is_expired = 0""",
				customer,
			)
			loyalty_balance = cint(result[0][0]) if result else 0

		# Update Customer
		frappe.db.set_value(
			"Customer",
			customer,
			{
				"ch_total_purchases": flt(total_purchases),
				"ch_total_services": cint(total_services),
				"ch_total_buybacks": cint(total_buybacks),
				"ch_active_devices": cint(active_devices),
				"ch_loyalty_points_balance": cint(loyalty_balance),
			},
			update_modified=False,
		)

		# Auto-classify segment
		_classify_customer_segment(customer, flt(total_purchases), cint(total_services))

	except Exception:
		frappe.log_error(
			title=f"CH Activity Summary Error: {customer}",
			message=frappe.get_traceback(),
		)


def _classify_customer_segment(customer, total_purchases, total_services):
	"""Auto-classify customer into a segment based on activity."""
	from frappe.utils import add_months, getdate

	customer_since = frappe.db.get_value("Customer", customer, "ch_customer_since")
	last_visit = frappe.db.get_value("Customer", customer, "ch_last_visit_date")

	segment = "New"
	total_txns = total_services + (1 if total_purchases > 0 else 0)

	if total_purchases >= 200000 or total_txns >= 10:
		segment = "VIP"
	elif total_txns >= 3:
		segment = "Regular"
	elif last_visit and getdate(last_visit) < getdate(add_months(today(), -6)):
		segment = "Dormant"
	elif customer_since and getdate(customer_since) < getdate(add_months(today(), -12)):
		if total_txns <= 1:
			segment = "Churned"

	frappe.db.set_value("Customer", customer, "ch_customer_segment", segment, update_modified=False)


def _find_customer_by_mobile(mobile):
	"""Find a Customer by mobile number."""
	if not mobile:
		return None
	mobile = mobile.strip().replace(" ", "").replace("-", "")
	if len(mobile) >= 10:
		mobile = mobile[-10:]
	return frappe.db.get_value("Customer", {"mobile_no": ("like", f"%{mobile}%")}, "name")
