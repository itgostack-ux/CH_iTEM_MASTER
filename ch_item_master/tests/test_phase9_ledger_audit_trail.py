"""Phase 9 smoke test - full VAS ledger audit trail.

Run:
	bench --site erpnext.local execute ch_item_master.tests.test_phase9_ledger_audit_trail.run
"""

import frappe
from frappe.utils import add_days, add_months, flt, nowdate


def _must(label, ok, detail=""):
	prefix = "[PASS]" if ok else "[FAIL]"
	print(f"  {prefix} {label}{(' - ' + detail) if detail else ''}")
	if not ok:
		raise AssertionError(label)


def _find_template_context() -> dict:
	plan = frappe.db.get_value(
		"CH Sold Plan",
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
		"max_claims": max(plan.get("max_claims") or 0, 2),
		"deductible_amount": plan.get("deductible_amount") or 0,
	}


def run():
	print("Phase 9 - Full Ledger Audit Trail Smoke")

	from ch_item_master.ch_item_master.warranty_api import expire_sold_plans, initiate_warranty_claim

	ctx = _find_template_context()
	_must("Active warranty-plan context available", bool(ctx), str(ctx))
	_must("Company available", bool(ctx.get("company")), str(ctx))
	_must("Customer available", bool(ctx.get("customer")), str(ctx))
	_must("Item available", bool(ctx.get("item_code")), str(ctx))
	_must("Warranty plan available", bool(ctx.get("warranty_plan")), str(ctx))

	today = nowdate()
	serial_no = f"PHASE9-{frappe.generate_hash(length=10)}"
	sold_plan = frappe.get_doc(
		{
			"doctype": "CH Sold Plan",
			"company": ctx["company"],
			"warranty_plan": ctx["warranty_plan"],
			"customer": ctx["customer"],
			"item_code": ctx["item_code"],
			"serial_no": serial_no,
			"start_date": today,
			"end_date": add_months(today, 12),
			"max_claims": max(ctx.get("max_claims") or 0, 2),
			"deductible_amount": flt(ctx.get("deductible_amount") or 0),
		}
	)
	claim_name = None

	try:
		sold_plan.insert(ignore_permissions=True)
		sold_plan.submit()
		_must("Sold plan submitted", bool(sold_plan.name), sold_plan.name)

		claim_result = initiate_warranty_claim(
			serial_no=serial_no,
			customer=ctx["customer"],
			item_code=ctx["item_code"],
			company=ctx["company"],
			issue_description="Phase 9 smoke claim",
			issue_category=None,
			reported_at_company=ctx["company"],
			reported_at_store="PHASE9-SMOKE",
			estimated_repair_cost=500,
			sold_plan=sold_plan.name,
			mode_of_service="Walk-in",
		)
		claim_name = claim_result.get("claim_name")
		_must("Warranty claim created", bool(claim_name), str(claim_result))

		claim_doc = frappe.get_doc("CH Warranty Claim", claim_name)
		_must("Claim linked to sold plan", claim_doc.sold_plan == sold_plan.name, claim_doc.sold_plan or "")

		if claim_doc.claim_status == "Pending Approval":
			claim_doc.approve(remarks="Phase 9 smoke approval")
			claim_doc.reload()

		if claim_doc.claim_status == "Ticket Created":
			claim_doc.mark_repair_complete(remarks="Phase 9 smoke repair done")
			claim_doc.reload()

		_must(
			"Claim status closable",
			claim_doc.claim_status in ("Approved", "Repair Complete", "Delivered", "Final QC Passed"),
			f"status={claim_doc.claim_status}",
		)

		claim_doc.close_claim(remarks="Phase 9 smoke settled")
		claim_doc.reload()
		sold_plan.reload()

		_must("Claim closed", claim_doc.claim_status == "Closed", f"status={claim_doc.claim_status}")
		_must("Sold plan claims_used incremented once", (sold_plan.claims_used or 0) == 1, f"claims_used={sold_plan.claims_used}")

		frappe.db.set_value(
			"CH Sold Plan",
			sold_plan.name,
			"end_date",
			add_days(today, -1),
			update_modified=False,
		)
		frappe.db.set_value(
			"CH Sold Plan",
			sold_plan.name,
			"status",
			"Active",
			update_modified=False,
		)
		frappe.db.commit()

		expire_sold_plans()
		sold_plan.reload()
		_must("Sold plan expired", sold_plan.status == "Expired", f"status={sold_plan.status}")

		events = frappe.get_all(
			"CH VAS Ledger",
			filters={"sold_plan": sold_plan.name},
			fields=["event_type", "claim_amount", "remaining_claims", "reference_name", "remarks"],
			order_by="creation asc",
		)
		event_types = [row.event_type for row in events]
		_must(
			"Expected ledger events present",
			all(event in event_types for event in ("Plan Activated", "Claim Used", "Plan Expired")),
			str(event_types),
		)

		activated_idx = event_types.index("Plan Activated")
		claim_idx = event_types.index("Claim Used")
		expired_idx = event_types.index("Plan Expired")
		_must(
			"Ledger events ordered by lifecycle",
			activated_idx < claim_idx < expired_idx,
			str(event_types),
		)

		claim_event = next(row for row in events if row.event_type == "Claim Used")
		expired_event = next(row for row in events if row.event_type == "Plan Expired")
		_must("Claim Used references claim", claim_event.reference_name == claim_name, str(claim_event))
		_must("Claim Used amount tracked", flt(claim_event.claim_amount) == 500, str(claim_event))
		_must("Claim Used remaining_claims decremented", flt(claim_event.remaining_claims) == 1, str(claim_event))
		_must(
			"Plan Expired remarks tracked",
			"Auto-expired" in (expired_event.remarks or ""),
			str(expired_event),
		)
	finally:
		if claim_name and frappe.db.exists("CH Warranty Claim", claim_name):
			try:
				claim_doc = frappe.get_doc("CH Warranty Claim", claim_name)
				if claim_doc.docstatus == 1:
					claim_doc.cancel()
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"Phase 9 cleanup failed for claim {claim_name}")

		if sold_plan.name and frappe.db.exists("CH Sold Plan", sold_plan.name):
			try:
				if frappe.db.get_value("CH Sold Plan", sold_plan.name, "docstatus") == 1:
					frappe.get_doc("CH Sold Plan", sold_plan.name).cancel()
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"Phase 9 cleanup failed for {sold_plan.name}")

	print("Phase 9 - ALL PASS")