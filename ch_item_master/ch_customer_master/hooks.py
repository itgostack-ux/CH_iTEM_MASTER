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


def on_buyback_assessment_update(doc, method=None):
	"""When a Buyback Assessment is created/saved, log a visit (only once)."""
	# Only log on first save — skip subsequent edits
	if not doc.is_new() and not doc.has_value_changed("deal_status"):
		return

	# Use the customer Link field directly (auto-linked by buyback_request.py)
	customer = doc.get("customer")
	if not customer:
		# Fallback: try mobile match
		customer = _find_customer_by_mobile(doc.get("mobile_no"))
	if not customer:
		return

	_log_store_visit(
		customer=customer,
		company=doc.get("company") or frappe.defaults.get_defaults().get("company", ""),
		visit_type="Buyback",
		reference_doctype="Buyback Assessment",
		reference_name=doc.name,
		staff=doc.owner,
	)
	_update_activity_summary(customer)


def _log_store_visit(customer, company, visit_type, reference_doctype=None,
					 reference_name=None, store=None, staff=None):
	"""Create a CH Customer Store Visit entry in the Customer's child table.

	Uses direct child-row insert + frappe.db.set_value for parent fields to avoid
	a full Customer.save() (which can trigger unrelated mandatory/validate errors
	such as tax_category, GST, or address validations on legacy customers).
	"""
	try:
		# Determine next idx for the child table
		next_idx = (frappe.db.sql(
			"""SELECT IFNULL(MAX(idx), 0) + 1 FROM `tabCH Customer Store Visit`
			WHERE parent = %s AND parenttype = 'Customer' AND parentfield = 'ch_stores_visited'""",
			customer,
		) or [[1]])[0][0]

		child = frappe.get_doc({
			"doctype": "CH Customer Store Visit",
			"parent": customer,
			"parenttype": "Customer",
			"parentfield": "ch_stores_visited",
			"idx": next_idx,
			"visit_date": today(),
			"store": store,
			"company": company,
			"visit_type": visit_type,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"staff": staff,
		})
		child.flags.ignore_permissions = True
		child.flags.ignore_validate = True
		child.flags.ignore_mandatory = True
		child.db_insert()

		# Update last visit info on parent (no validations / no save)
		updates = {"ch_last_visit_date": today()}
		if store:
			updates["ch_last_visit_store"] = (
				frappe.db.get_value("Warehouse", store, "warehouse_name") or store
			)
		frappe.db.set_value("Customer", customer, updates, update_modified=False)
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

		# Total buybacks — use customer Link field if available, fallback to mobile
		total_buybacks = 0
		if frappe.db.exists("DocType", "Buyback Request"):
			# Primary: count by customer Link (new field)
			total_buybacks = frappe.db.count(
				"Buyback Request", {"customer": customer}
			)
			# Fallback: also count by mobile match for older records without customer link
			if not total_buybacks:
				mobile = frappe.db.get_value("Customer", customer, "mobile_no")
				if mobile:
					mobile10 = mobile.strip().replace(" ", "").replace("-", "")[-10:]
					total_buybacks = frappe.db.count(
						"Buyback Request", {"mobile_no": mobile10}
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

		# Active sold plans
		active_plans = 0
		if frappe.db.exists("DocType", "CH Sold Plan"):
			active_plans = frappe.db.count(
				"CH Sold Plan",
				{"customer": customer, "docstatus": 1, "status": "Active"},
			)

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
				"ch_active_plans_count": cint(active_plans),
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


def on_sold_plan_change(doc, method=None):
	"""When a CH Sold Plan is submitted/cancelled/status-changed, sync active plans count."""
	if doc.customer:
		_update_activity_summary(doc.customer)


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
	"""Find a Customer by exact mobile number match (last 10 digits)."""
	if not mobile:
		return None
	mobile = mobile.strip().replace(" ", "").replace("-", "")
	if mobile.startswith("+91"):
		mobile = mobile[3:]
	elif mobile.startswith("91") and len(mobile) == 12:
		mobile = mobile[2:]
	if len(mobile) < 10:
		return None
	mobile10 = mobile[-10:]
	# Exact match — try bare 10 digits, then +91 prefix, then 91 prefix
	customer = frappe.db.get_value("Customer", {"mobile_no": mobile10}, "name")
	if not customer:
		customer = frappe.db.get_value("Customer", {"mobile_no": f"+91{mobile10}"}, "name")
	if not customer:
		customer = frappe.db.get_value("Customer", {"mobile_no": f"91{mobile10}"}, "name")
	return customer
