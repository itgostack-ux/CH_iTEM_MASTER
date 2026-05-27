"""Phase 5 smoke test - customer channels and self-service.

Run:
	bench --site erpnext.local execute ch_item_master.tests.test_phase5_customer_channels.run
"""

import frappe


def _must(label, ok, detail=""):
	prefix = "[PASS]" if ok else "[FAIL]"
	print(f"  {prefix} {label}{(' - ' + detail) if detail else ''}")
	if not ok:
		raise AssertionError(label)


def _get_or_create_customer() -> tuple[str, str]:
	mobile_no = "9876501234"
	customer = frappe.db.get_value("Customer", {"mobile_no": mobile_no}, "name")
	if customer:
		return customer, mobile_no

	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": "Phase 5 Portal Smoke",
			"customer_type": "Individual",
			"customer_group": frappe.db.get_value("Customer Group", {}, "name") or "All Customer Groups",
			"territory": frappe.db.get_value("Territory", {}, "name") or "All Territories",
			"mobile_no": mobile_no,
			"email_id": "phase5@example.com",
		}
	).insert(ignore_permissions=True)
	return customer.name, mobile_no


def _get_or_create_item() -> str:
	item_code = frappe.db.get_value("Item", {"disabled": 0}, "name")
	if item_code:
		return item_code

	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": "PHASE5-PORTAL-ITEM",
			"item_name": "Phase 5 Portal Item",
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
			"stock_uom": frappe.db.get_value("UOM", {}, "name") or "Nos",
			"is_sales_item": 1,
		}
	).insert(ignore_permissions=True)
	return item.name


def run():
	print("Phase 5 - Customer Channels Smoke")

	from buyback.public_portal_api import get_public_quote_estimate, get_quote_grades, submit_public_quote_request
	from ch_item_master.ch_core import whatsapp_webhook
	from ch_item_master.ch_customer_master.customer_portal_api import _create_portal_session, get_dashboard, get_store_locator

	customer, mobile_no = _get_or_create_customer()
	item_code = _get_or_create_item()
	_must("Customer available", bool(customer), customer)
	_must("Item available", bool(item_code), item_code)

	grades = get_quote_grades()
	_must("Quote grades API works", isinstance(grades, list) and bool(grades))

	grade_name = grades[0].get("name") if grades else ""
	estimate = get_public_quote_estimate(item_code=item_code, grade=grade_name)
	_must("Public quote estimate shape", isinstance(estimate, dict) and estimate.get("item_code") == item_code, str(estimate))

	assessment = submit_public_quote_request(
		customer_name="Phase 5 Portal Smoke",
		mobile_no=mobile_no,
		item_code=item_code,
		grade=grade_name,
		remarks="Phase 5 smoke",
	)
	_must("Public quote request created assessment", bool(assessment.get("name")), str(assessment))

	session_token = _create_portal_session(mobile_no, customer)
	dashboard = get_dashboard(session_token=session_token)
	_must("Portal dashboard returns profile", dashboard.get("profile", {}).get("mobile_no") == mobile_no)
	_must("Portal dashboard includes buyback rows", isinstance(dashboard.get("buyback_assessments"), list))
	stores = get_store_locator(limit=3)
	_must("Store locator API works", isinstance(stores, list))

	unique_template = f"phase5_webhook_{frappe.generate_hash(length=6)}"
	original_loader = whatsapp_webhook._request_payload
	try:
		whatsapp_webhook._request_payload = lambda: {
			"recipient": {"phone": mobile_no},
			"template_name": unique_template,
			"status": "delivered",
		}
		result = whatsapp_webhook.gallabox_webhook()
	finally:
		whatsapp_webhook._request_payload = original_loader

	_must("Webhook returns ok", bool(result.get("ok")), str(result))
	wa_log = frappe.db.get_value("CH WhatsApp Log", {"template_name": unique_template}, ["name", "status"], as_dict=True)
	_must("Webhook log inserted", bool(wa_log and wa_log.get("name")), str(wa_log))
	_must("Webhook log status updated", (wa_log or {}).get("status") == "Sent", str(wa_log))

	print("Phase 5 - ALL PASS")
