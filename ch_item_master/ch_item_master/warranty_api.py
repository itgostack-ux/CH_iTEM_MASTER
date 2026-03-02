# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Item Master — Warranty API.

Central entrypoint for warranty operations consumed by downstream apps (GoFix, etc.).
All functions are whitelisted for client/API access.

Usage from GoFix:
    from ch_item_master.ch_item_master.warranty_api import check_warranty, get_applicable_plans
"""

import frappe
from frappe import _
from frappe.utils import nowdate, getdate, add_months


# ── Warranty Lookup ──────────────────────────────────────────────────────────

@frappe.whitelist()
def check_warranty(serial_no, company=None):
	"""Check warranty status for a device serial/IMEI.

	Looks up CH Sold Plan records (submitted, active) for the serial.
	Falls back to CH Serial Lifecycle warranty fields if no Sold Plan exists.

	Args:
		serial_no: Serial number or IMEI to look up.
		company: Optional company filter.

	Returns:
		dict with: warranty_covered, warranty_status, covering_plan, all_plans,
		           serial_lifecycle (if exists), deductible_amount
	"""
	from ch_item_master.ch_item_master.doctype.ch_sold_plan.ch_sold_plan import (
		check_warranty_status,
	)

	result = check_warranty_status(serial_no, company)

	# Enrich with Serial Lifecycle data
	if frappe.db.exists("CH Serial Lifecycle", serial_no):
		lc = frappe.db.get_value(
			"CH Serial Lifecycle",
			serial_no,
			[
				"lifecycle_status", "warranty_status", "warranty_plan",
				"warranty_start_date", "warranty_end_date", "extended_warranty_end",
				"item_code", "item_name", "customer", "customer_name",
				"service_count", "last_service_date",
			],
			as_dict=True,
		)
		result["serial_lifecycle"] = lc
	else:
		# Try IMEI lookup
		name = frappe.db.get_value(
			"CH Serial Lifecycle", {"imei_number": serial_no}, "name"
		) or frappe.db.get_value(
			"CH Serial Lifecycle", {"imei_number_2": serial_no}, "name"
		)
		if name:
			lc = frappe.db.get_value(
				"CH Serial Lifecycle",
				name,
				[
					"lifecycle_status", "warranty_status", "warranty_plan",
					"warranty_start_date", "warranty_end_date", "extended_warranty_end",
					"item_code", "item_name", "customer", "customer_name",
					"service_count", "last_service_date",
				],
				as_dict=True,
			)
			result["serial_lifecycle"] = lc
		else:
			result["serial_lifecycle"] = None

	# Deductible from covering plan
	if result.get("covering_plan"):
		result["deductible_amount"] = result["covering_plan"].get("deductible_amount", 0)
	else:
		result["deductible_amount"] = 0

	return result


@frappe.whitelist()
def get_applicable_plans(item_code=None, item_group=None, channel=None,
                         company=None, brand=None):
	"""Get warranty/VAS plans applicable to an item.

	Delegates to CH Warranty Plan.get_applicable_plans with all filters.
	"""
	from ch_item_master.ch_item_master.doctype.ch_warranty_plan.ch_warranty_plan import (
		CHWarrantyPlan,
	)
	return CHWarrantyPlan.get_applicable_plans(
		item_code=item_code,
		item_group=item_group,
		channel=channel,
		company=company,
		brand=brand,
	)


# ── Plan Issuance ────────────────────────────────────────────────────────────

@frappe.whitelist()
def issue_warranty_plan(warranty_plan, customer, item_code, serial_no=None,
                        start_date=None, company=None, sales_invoice=None,
                        sales_order=None, plan_price=None):
	"""Issue (create + submit) a Sold Plan for a customer/device.

	Called by GoFix or retail sales flow when a warranty/VAS plan is sold.

	Args:
		warranty_plan: CH Warranty Plan name
		customer: Customer name
		item_code: Device item_code
		serial_no: Serial/IMEI (optional for non-serialized items)
		start_date: Coverage start (defaults to today)
		company: Company (defaults from plan)
		sales_invoice: Linked Sales Invoice
		sales_order: Linked Sales Order
		plan_price: Actual price charged

	Returns:
		dict with: sold_plan name, status
	"""
	if not start_date:
		start_date = nowdate()

	plan = frappe.get_doc("CH Warranty Plan", warranty_plan)

	if not company:
		company = plan.company

	# Calculate end_date
	end_date = None
	if plan.duration_months:
		end_date = add_months(start_date, plan.duration_months)

	doc = frappe.new_doc("CH Sold Plan")
	doc.update({
		"company": company,
		"warranty_plan": warranty_plan,
		"customer": customer,
		"item_code": item_code,
		"serial_no": serial_no,
		"start_date": start_date,
		"end_date": end_date,
		"sales_invoice": sales_invoice,
		"sales_order": sales_order,
		"plan_price": plan_price or plan.price,
	})

	doc.insert(ignore_permissions=True)
	doc.submit()

	return {"sold_plan": doc.name, "status": doc.status, "end_date": str(doc.end_date)}


# ── Claim Recording ─────────────────────────────────────────────────────────

@frappe.whitelist()
def record_warranty_claim(serial_no, service_reference=None, company=None):
	"""Record a warranty claim against the best available plan for a serial.

	Finds the most applicable active plan and increments claims_used.

	Args:
		serial_no: Serial/IMEI
		service_reference: Optional reference to service request/order
		company: Optional company filter

	Returns:
		dict with: sold_plan, claims_used, max_claims, deductible_amount
	"""
	from ch_item_master.ch_item_master.doctype.ch_sold_plan.ch_sold_plan import (
		get_active_plans_for_serial,
	)

	plans = get_active_plans_for_serial(serial_no, company)
	valid_plans = [p for p in plans if p.get("is_valid")]

	if not valid_plans:
		frappe.throw(
			_("No active warranty plan found for serial {0}").format(
				frappe.bold(serial_no)
			),
			title=_("No Warranty Coverage"),
		)

	# Prefer Own Warranty > Extended > VAS > Protection
	priority = {
		"Own Warranty": 1, "Extended Warranty": 2,
		"Value Added Service": 3, "Protection Plan": 4,
	}
	valid_plans.sort(key=lambda p: priority.get(p.get("plan_type"), 99))

	best_plan = valid_plans[0]
	doc = frappe.get_doc("CH Sold Plan", best_plan["name"])
	doc.record_claim(service_reference=service_reference)

	return {
		"sold_plan": doc.name,
		"plan_title": doc.plan_title,
		"claims_used": doc.claims_used,
		"max_claims": doc.max_claims,
		"deductible_amount": doc.deductible_amount or 0,
	}


# ── MSP Validation ──────────────────────────────────────────────────────────

@frappe.whitelist()
def validate_msp(item_code, selling_rate):
	"""Check if a selling rate meets the Minimum Selling Price for an item.

	Args:
		item_code: Item code to check
		selling_rate: Proposed selling rate

	Returns:
		dict with: is_valid, msp, item_code, message
	"""
	selling_rate = float(selling_rate or 0)

	msp_data = frappe.db.get_value(
		"Item", item_code,
		["ch_minimum_selling_price", "ch_msp_effective_from"],
		as_dict=True,
	)

	if not msp_data or not msp_data.ch_minimum_selling_price:
		return {"is_valid": True, "msp": 0, "item_code": item_code, "message": "No MSP configured"}

	msp = msp_data.ch_minimum_selling_price

	# Check if MSP is in effect
	if msp_data.ch_msp_effective_from:
		if getdate(nowdate()) < getdate(msp_data.ch_msp_effective_from):
			return {"is_valid": True, "msp": msp, "item_code": item_code,
			        "message": f"MSP not yet effective (starts {msp_data.ch_msp_effective_from})"}

	if selling_rate < msp:
		return {
			"is_valid": False,
			"msp": msp,
			"item_code": item_code,
			"message": f"Rate {selling_rate} is below MSP {msp}. Approval required.",
		}

	return {"is_valid": True, "msp": msp, "item_code": item_code, "message": "OK"}


# ── Auto-expiry (Scheduled Task) ────────────────────────────────────────────

def expire_sold_plans():
	"""Mark expired CH Sold Plans. Called by scheduled task (daily).

	Finds all Active sold plans where end_date < today and sets status to Expired.
	"""
	today = nowdate()
	expired = frappe.get_all(
		"CH Sold Plan",
		filters={
			"status": "Active",
			"docstatus": 1,
			"end_date": ("<", today),
		},
		pluck="name",
	)

	for name in expired:
		frappe.db.set_value("CH Sold Plan", name, "status", "Expired", update_modified=False)

	if expired:
		frappe.db.commit()
		frappe.logger("ch_item_master").info(
			f"Auto-expired {len(expired)} sold plans: {expired[:10]}{'...' if len(expired) > 10 else ''}"
		)
