"""Phase 7 smoke test - active VAS plan state and claim count.

Run:
	bench --site erpnext.local execute ch_item_master.tests.test_phase7_sold_plan_state.run
"""

import frappe
from frappe.utils import add_months, flt, nowdate


def _must(label, ok, detail=""):
	prefix = "[PASS]" if ok else "[FAIL]"
	print(f"  {prefix} {label}{(' - ' + detail) if detail else ''}")
	if not ok:
		raise AssertionError(label)


def _find_template_context() -> dict:
	plan = frappe.db.get_value(
		"Active VAS Plans",
		{"docstatus": 1, "status": "Active"},
		["name", "company", "warranty_plan", "customer", "item_code", "max_claims", "deductible_amount"],
		as_dict=True,
	)
	if plan:
		return plan

	plan = frappe.db.get_value(
		"CH Warranty Plan",
		{"status": "Active"},
		["name", "plan_name", "duration_months", "max_claims", "deductible_amount"],
		as_dict=True,
	)
	if not plan:
		return {}

	return {
		"company": frappe.get_cached_value("Global Defaults", None, "default_company")
			or frappe.db.get_value("Company", {}, "name"),
		"warranty_plan": plan.name,
		"customer": frappe.db.get_value("Customer", {}, "name"),
		"item_code": frappe.db.get_value("Item", {"disabled": 0}, "name"),
		"max_claims": plan.get("max_claims") or 1,
		"deductible_amount": plan.get("deductible_amount") or 0,
	}


def run():
	print("Phase 7 - Active VAS Plan State Smoke")

	from ch_item_master.ch_item_master.doctype.active_vas_plans.active_vas_plans import CHSoldPlan

	ctx = _find_template_context()
	_must("Active warranty-plan context available", bool(ctx), str(ctx))
	_must("Company available", bool(ctx.get("company")), str(ctx))
	_must("Customer available", bool(ctx.get("customer")), str(ctx))
	_must("Item available", bool(ctx.get("item_code")), str(ctx))
	_must("Warranty plan available", bool(ctx.get("warranty_plan")), str(ctx))

	today = nowdate()
	serial_no = f"PHASE7-{frappe.generate_hash(length=10)}"
	sold_plan = frappe.get_doc(
		{
			"doctype": "Active VAS Plans",
			"company": ctx["company"],
			"warranty_plan": ctx["warranty_plan"],
			"customer": ctx["customer"],
			"item_code": ctx["item_code"],
			"serial_no": serial_no,
			"start_date": today,
			"end_date": add_months(today, 12),
			"max_claims": ctx.get("max_claims") or 1,
			"deductible_amount": flt(ctx.get("deductible_amount") or 0),
		}
	)

	try:
		sold_plan.insert(ignore_permissions=True)
		sold_plan.submit()
		_must("Active VAS Plan submitted", bool(sold_plan.name), sold_plan.name)

		baseline_claims = sold_plan.claims_used or 0
		sold_plan.record_claim(service_reference="PHASE7-SMOKE", claim_cost=250)
		sold_plan.reload()

		_must(
			"Active VAS Plan claims_used incremented",
			(sold_plan.claims_used or 0) == baseline_claims + 1,
			f"claims_used={sold_plan.claims_used}",
		)
		_must(
			"Active VAS Plan status remains valid after claim",
			sold_plan.status in ("Active", "Claimed"),
			f"status={sold_plan.status}",
		)
	finally:
		if sold_plan.name and frappe.db.exists("Active VAS Plans", sold_plan.name):
			try:
				if frappe.db.get_value("Active VAS Plans", sold_plan.name, "docstatus") == 1:
					frappe.get_doc("Active VAS Plans", sold_plan.name).cancel()
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"Phase 7 cleanup failed for {sold_plan.name}")

	print("Phase 7 - ALL PASS")