"""VAS benefit rule + active plan snapshot E2E.

Run:
  bench --site erpnext.local execute ch_item_master.tests.test_vas_active_plan_snapshot_e2e.run
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import flt, nowdate

from ch_item_master.ch_item_master.warranty_api import issue_warranty_plan, validate_claim


class TestFailure(AssertionError):
	pass


def _assert(condition, message):
	if not condition:
		raise TestFailure(message)


def _pick_company():
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if company:
		return company
	return frappe.db.get_value("Company", {}, "name")


def _pick_customer():
	customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
	_assert(customer, "No active Customer available for VAS snapshot test")
	return customer


def _pick_device_item():
	item = frappe.db.get_value(
		"Item",
		{"disabled": 0, "is_stock_item": 1, "is_sales_item": 1},
		"name",
	)
	_assert(item, "No active stock sales Item available for VAS snapshot test")
	return item


def _pick_service_item():
	item = frappe.db.get_value(
		"Item",
		{"disabled": 0, "is_stock_item": 0, "is_sales_item": 1},
		"name",
	)
	_assert(item, "No active non-stock sales Item available for VAS snapshot test")
	return item


def _ensure_issue_category(name):
	existing = frappe.db.get_value("Issue Category", {"category_name": name}, "name")
	if existing:
		return existing

	doc = frappe.new_doc("Issue Category")
	doc.category_name = name
	doc.is_active = 1
	doc.insert(ignore_permissions=True)
	return doc.name


def run() -> dict:
	savepoint = "vas_active_plan_snapshot_e2e"
	frappe.db.savepoint(savepoint)
	try:
		company = _pick_company()
		customer = _pick_customer()
		device_item = _pick_device_item()
		service_item = _pick_service_item()
		issue_type = _ensure_issue_category("VAS Snapshot Screen Issue")
		plan_name = f"VAS Snapshot Plan {frappe.generate_hash(length=8)}"

		plan = frappe.new_doc("CH Warranty Plan")
		plan.company = company
		plan.plan_name = plan_name
		plan.plan_type = "Protection Plan"
		plan.coverage_scope = "Screen Only"
		plan.service_item = service_item
		plan.status = "Active"
		plan.duration_months = 12
		plan.max_claims = 2
		plan.claims_per_year = 1
		plan.deductible_amount = 0
		plan.price = 999
		plan.pricing_mode = "Fixed"
		plan.fulfillment_type = "Digital Activation"
		plan.append(
			"benefit_rules",
			{
				"benefit_code": "SCREEN_DIGITAL_CARE",
				"benefit_name": "Screen digital care entitlement",
				"benefit_type": "Repair Coverage",
				"fulfillment_type": "Digital Activation",
				"covered": 1,
				"entitlement_basis": "Per Plan",
				"unit_limit": 1,
				"value_limit": 3000,
				"coverage_percent": 50,
				"deductible_amount": 100,
				"issue_type": issue_type,
			},
		)
		plan.insert(ignore_permissions=True)

		serial_no = f"VAS-SNAPSHOT-{frappe.generate_hash(length=8).upper()}"
		issued = issue_warranty_plan(
			warranty_plan=plan.name,
			customer=customer,
			item_code=device_item,
			serial_no=serial_no,
			start_date=nowdate(),
			company=company,
			plan_price=799,
			external_device_source="E2E VAS Snapshot Test",
		)
		_assert(issued.get("fulfillment_type") == "Digital Activation", "Issue API did not return fulfillment type")

		active_plan = frappe.get_doc("Active VAS Plans", issued["active_plan"])
		_assert(active_plan.docstatus == 1, "Active plan was not submitted")
		_assert(active_plan.fulfillment_type == "Digital Activation", "Active plan did not snapshot fulfillment type")

		snapshot = json.loads(active_plan.plan_snapshot_json or "{}")
		benefits = json.loads(active_plan.benefit_snapshot_json or "[]")
		_assert(snapshot.get("plan_name") == plan_name, "Plan snapshot missing plan name")
		_assert(snapshot.get("fulfillment_type") == "Digital Activation", "Plan snapshot missing fulfillment type")
		_assert(len(benefits) == 1, "Benefit snapshot should contain exactly one benefit")
		_assert(benefits[0].get("value_limit") == 3000, "Benefit value limit was not snapshotted")

		original_snapshot_json = active_plan.plan_snapshot_json
		plan.price = 1999
		plan.benefit_rules[0].value_limit = 100
		plan.benefit_rules[0].coverage_percent = 10
		plan.save(ignore_permissions=True)

		active_plan.reload()
		_assert(active_plan.plan_snapshot_json == original_snapshot_json, "Active plan snapshot changed after master edit")

		claim_check = validate_claim(active_plan.name, issue_type=issue_type, estimate_amount=10000)
		_assert(claim_check.get("eligible"), f"Snapshot-backed claim should be eligible: {claim_check}")
		_assert(claim_check.get("benefit_match") == "SCREEN_DIGITAL_CARE", "Claim did not match active plan benefit snapshot")
		_assert(flt(claim_check.get("covered_amount")) == 3000, f"Expected snapshot value cap 3000, got {claim_check}")
		_assert(flt(claim_check.get("customer_payable")) == 7000, f"Expected customer payable 7000, got {claim_check}")

		print("PASS: VAS benefit rules, fulfillment type and active-plan snapshots work end to end")
		return {"pass": 1, "fail": 0}
	finally:
		frappe.db.rollback(save_point=savepoint)
