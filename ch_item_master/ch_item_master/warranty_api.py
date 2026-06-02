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
from frappe.rate_limiter import rate_limit
from frappe.utils import nowdate, now_datetime, getdate, add_months, flt

from ch_item_master.security import get_company_filter_value, get_company_scope


# ── Warranty Lookup ──────────────────────────────────────────────────────────

@frappe.whitelist()
def check_warranty(serial_no, company=None) -> dict:
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

	# Enrich with Serial Lifecycle data — uses the same resolution helper
	# as CH Warranty Claim so callers see identical behaviour for
	# name / imei_number / imei_number_2 lookups.
	from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
		resolve_lifecycle_name,
	)

	lc_name = resolve_lifecycle_name(serial_no)
	if lc_name:
		lc = frappe.db.get_value(
			"CH Serial Lifecycle",
			lc_name,
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
                         company=None, brand=None) -> dict:
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


# ── VAS Category Validation ─────────────────────────────────────────────────

@frappe.whitelist()
def validate_vas_category(serial_no, warranty_plan) -> dict:
	"""Validate that a VAS plan's category restriction matches the device's category.

	Used by POS when manual IMEI is entered to ensure laptop plans aren't sold for phones, etc.

	Returns:
		dict: valid (bool), item_code, category, message
	"""
	if not serial_no or not warranty_plan:
		return {"valid": False, "message": _("Serial number and warranty plan are required")}

	# Look up item from Serial No
	item_code = frappe.db.get_value("Serial No", serial_no, "item_code")
	if not item_code:
		# Serial not found — allow anyway (truly external device)
		return {"valid": True, "item_code": None, "category": None,
		        "message": _("Serial not found in system — no category restriction applied")}

	# Get category of the item
	category = frappe.db.get_value("Item", item_code, "ch_category")

	# Get plan's applicable categories
	plan_categories = frappe.get_all(
		"CH Warranty Plan Category",
		filters={"parent": warranty_plan},
		pluck="category",
	)

	if not plan_categories:
		# No category restriction on this plan
		return {"valid": True, "item_code": item_code, "category": category}

	if not category:
		return {"valid": False, "item_code": item_code, "category": None,
		        "message": _("Device has no category set — cannot verify eligibility")}

	if category not in plan_categories:
		return {"valid": False, "item_code": item_code, "category": category,
		        "message": _("This plan is for {0} only, but the device is {1}").format(
		            ", ".join(plan_categories), category)}

	return {"valid": True, "item_code": item_code, "category": category}


# ── Plan Issuance ────────────────────────────────────────────────────────────

@frappe.whitelist()
def issue_warranty_plan(warranty_plan, customer, item_code, serial_no=None,
                        start_date=None, company=None, sales_invoice=None,
                        sales_order=None, plan_price=None) -> dict:
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

	frappe.has_permission("CH Sold Plan", "create", throw=True)

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
		"max_claims": plan.max_claims or 0,
		"deductible_amount": plan.deductible_amount or 0,
		"claims_per_year": plan.claims_per_year or 0,
	})

	doc.insert(ignore_permissions=True)
	doc.submit()

	return {"sold_plan": doc.name, "status": doc.status, "end_date": str(doc.end_date)}


# ── Claim Recording ─────────────────────────────────────────────────────────

@frappe.whitelist()
def record_warranty_claim(serial_no, service_reference=None, company=None) -> dict:
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

	# Sort by plan-level priority (higher = preferred), then by type-based default
	type_priority = {
		"Own Warranty": 1, "Extended Warranty": 2,
		"Value Added Service": 3, "Protection Plan": 4,
		"Post-Repair Warranty": 5,
	}

	def _plan_sort_key(p):
		# Plans with explicit priority (set on CH Warranty Plan) come first (higher value first)
		plan_prio = 0
		if p.get("warranty_plan"):
			plan_prio = frappe.db.get_value(
				"CH Warranty Plan", p["warranty_plan"], "priority"
			) or 0
		# Negate so higher priority sorts first; then use type-based default
		return (-int(plan_prio), type_priority.get(p.get("plan_type"), 99))

	valid_plans.sort(key=_plan_sort_key)

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
def validate_claim(sold_plan_name, issue_type=None, estimate_amount=0) -> dict:
	"""Pre-validate whether a claim is eligible under a sold plan.

	Checks expiry, claim count limits, annual limits, value caps,
	and per-issue coverage rules (from CH Coverage Rule child table).

	Args:
		sold_plan_name: CH Sold Plan name
		issue_type: Issue Category name (optional — for coverage rule lookup)
		estimate_amount: Estimated repair cost

	Returns:
		dict with: eligible (bool), covered_amount, customer_payable,
		           deductible, coverage_percent, reason
	"""
	estimate_amount = flt(estimate_amount)

	if not frappe.db.exists("CH Sold Plan", sold_plan_name):
		return {"eligible": False, "reason": _("Sold Plan {0} not found").format(sold_plan_name)}

	sp = frappe.get_doc("CH Sold Plan", sold_plan_name)

	# ── Basic eligibility ─────────────────────────────────────────────
	if sp.docstatus != 1:
		return {"eligible": False, "reason": _("Plan is not submitted")}

	if sp.status != "Active":
		return {"eligible": False, "reason": _("Plan status is {0}").format(sp.status)}

	today = getdate(nowdate())
	if sp.end_date and today > getdate(sp.end_date):
		return {"eligible": False, "reason": _("Plan expired on {0}").format(sp.end_date)}

	# ── Lifetime claim count ──────────────────────────────────────────
	if sp.max_claims and sp.max_claims > 0:
		if (sp.claims_used or 0) >= sp.max_claims:
			return {"eligible": False,
			        "reason": _("All {0} claims exhausted").format(sp.max_claims)}

	# ── Annual claim limit ────────────────────────────────────────────
	if sp.claims_per_year and sp.claims_per_year > 0:
		year_claims = sp._count_claims_current_year()
		if year_claims >= sp.claims_per_year:
			return {"eligible": False,
			        "reason": _("Annual limit of {0} claims reached").format(sp.claims_per_year)}

	# ── Value cap ─────────────────────────────────────────────────────
	if sp.max_coverage_value and sp.max_coverage_value > 0:
		remaining_value = sp.max_coverage_value - flt(sp.total_claimed_value)
		if remaining_value <= 0:
			return {"eligible": False,
			        "reason": _("Coverage value fully consumed")}

	# ── Coverage rules lookup ─────────────────────────────────────────
	coverage_percent = 100
	deductible = flt(sp.deductible_amount)
	rule_match = None

	if issue_type and sp.warranty_plan:
		plan = frappe.get_doc("CH Warranty Plan", sp.warranty_plan)
		for rule in (plan.coverage_rules or []):
			if rule.issue_type == issue_type:
				rule_match = rule
				break

		if rule_match:
			if not rule_match.covered:
				return {"eligible": False,
				        "reason": _("Issue type '{0}' is not covered under this plan").format(issue_type)}

			coverage_percent = flt(rule_match.coverage_percent) or 100
			if rule_match.deductible_override is not None and flt(rule_match.deductible_override) > 0:
				deductible = flt(rule_match.deductible_override)

			# Per-issue claim limit
			if rule_match.max_claim_per_issue and rule_match.max_claim_per_issue > 0:
				issue_claims = frappe.db.count("CH Warranty Claim", {
					"sold_plan": sold_plan_name,
					"issue_category": issue_type,
					"docstatus": 1,
					"claim_status": ["not in", ["Cancelled", "Rejected"]],
				})
				if issue_claims >= rule_match.max_claim_per_issue:
					return {"eligible": False,
					        "reason": _("Max {0} claims for '{1}' already used").format(
					            rule_match.max_claim_per_issue, issue_type)}

	# ── Calculate amounts ─────────────────────────────────────────────
	covered_before_deductible = estimate_amount * (coverage_percent / 100)
	covered_amount = max(0, covered_before_deductible - deductible)
	customer_payable = estimate_amount - covered_amount

	# Cap at remaining coverage value
	if sp.max_coverage_value and sp.max_coverage_value > 0:
		remaining_value = sp.max_coverage_value - flt(sp.total_claimed_value)
		if covered_amount > remaining_value:
			covered_amount = remaining_value
			customer_payable = estimate_amount - covered_amount

	return {
		"eligible": True,
		"covered_amount": covered_amount,
		"customer_payable": customer_payable,
		"deductible": deductible,
		"coverage_percent": coverage_percent,
		"rule_match": rule_match.issue_type if rule_match else None,
		"reason": _("Claim eligible"),
	}


# ── MSP Validation ──────────────────────────────────────────────────────────

@frappe.whitelist()
def validate_msp(item_code, selling_rate) -> dict:
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

	# Log expiry events to VAS ledger
	if expired:
		try:
			from ch_item_master.ch_item_master.doctype.ch_vas_ledger.ch_vas_ledger import log_vas_event
			for name in expired:
				log_vas_event(
					sold_plan=name,
					event_type="Plan Expired",
					remarks="Auto-expired by scheduled task",
				)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				"VAS Ledger expiry logging failed",
			)

	if expired:
		frappe.db.commit()
		frappe.logger("ch_item_master").info(
			f"Auto-expired {len(expired)} sold plans: {expired[:10]}{'...' if len(expired) > 10 else ''}"
		)


# ── Customer Warranty Dashboard ──────────────────────────────────────────────

@frappe.whitelist()
def get_customer_warranty_dashboard(identifier, company=None) -> dict:
	"""Full customer warranty dashboard — search by phone, IMEI, or serial.

	Used by POS to give the store exec a complete view of a customer's warranty
	portfolio, like an insurance agent's policy dashboard.

	Args:
		identifier: Phone number, IMEI, or serial number.
		company: Optional company filter.

	Returns:
		dict with: customer, devices[], each device has plans[] and claims[].
		Plans are tagged with status: active/expired/exhausted/eligible.
	"""
	from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
		get_claims_for_serial,
	)

	company_scope = get_company_scope(requested_company=company)
	company_filter = get_company_filter_value(requested_company=company)

	customer = None
	customer_name = ""
	customer_phone = ""
	customer_email = ""
	search_type = None  # "phone", "serial", "imei"

	identifier = (identifier or "").strip()
	if not identifier:
		frappe.throw(_("Enter a phone number, IMEI, or serial number"), title=_("API Error"))

	# ── Detect search type and find customer ─────────────────────────
	# Phone: 10+ digits (Indian phone)
	digits_only = "".join(c for c in identifier if c.isdigit())
	is_phone = len(digits_only) >= 10 and len(digits_only) <= 13 and not identifier.startswith("MO/")

	if is_phone:
		search_type = "phone"
		# Search by mobile_no on Customer (exact or last 10 digits)
		phone_suffix = digits_only[-10:]
		customer = frappe.db.get_value(
			"Customer", {"mobile_no": ["like", f"%{phone_suffix}"]}, "name"
		)
		if not customer:
			# Try ch_alternate_phone
			customer = frappe.db.get_value(
				"Customer", {"ch_alternate_phone": ["like", f"%{phone_suffix}"]}, "name"
			)
		if not customer:
			# Try Contact → Dynamic Link → Customer
			contact = frappe.db.sql("""
				SELECT dl.link_name
				FROM `tabDynamic Link` dl
				JOIN `tabContact` c ON c.name = dl.parent
				WHERE dl.link_doctype = 'Customer'
				  AND c.mobile_no LIKE %s
				LIMIT 1
			""", (f"%{phone_suffix}",))
			if contact:
				customer = contact[0][0]

		if not customer:
			return {
				"found": False,
				"search_type": "phone",
				"message": _("No customer found with phone number {0}").format(identifier),
				"customer": None,
				"devices": [],
			}
	else:
		search_type = "serial"
		# Resolve a CH Serial Lifecycle row using the shared helper so that
		# IMEI / IMEI-2 / canonical-name lookups behave identically across
		# the warranty surface.
		from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
			resolve_lifecycle_name,
		)
		lc_name = resolve_lifecycle_name(identifier)

		if lc_name:
			customer = frappe.db.get_value("CH Serial Lifecycle", lc_name, "customer")

		# Fallback: ERPNext Serial No
		if not customer and frappe.db.exists("Serial No", identifier):
			customer = frappe.db.get_value("Serial No", identifier, "customer")
			if not customer:
				cd = _get_customer_from_serial(identifier)
				if cd:
					customer = cd.get("customer")

		# Fallback: check CH Sold Plan
		if not customer:
			customer = frappe.db.get_value(
				"CH Sold Plan",
				{"serial_no": identifier, "docstatus": 1},
				"customer",
			)

		if not customer:
			# Return the single device lookup as before
			return {
				"found": False,
				"search_type": "serial",
				"message": _("No customer found for device {0}").format(identifier),
				"customer": None,
				"devices": [],
			}

	# ── Get customer info ────────────────────────────────────────────
	cust_data = frappe.db.get_value(
		"Customer", customer,
		["name", "customer_name", "mobile_no", "ch_alternate_phone", "email_id"],
		as_dict=True,
	)
	if cust_data:
		customer_name = cust_data.customer_name
		customer_phone = cust_data.mobile_no or cust_data.ch_alternate_phone or ""
		customer_email = cust_data.email_id or ""

	# ── Determine which serials are visible for the requested company ─────────
	visible_serials = set()
	if company_scope:
		if frappe.db.exists("DocType", "CH Customer Device"):
			visible_serials.update(filter(None, frappe.get_all(
				"CH Customer Device",
				filters={"customer": customer, "company": company_filter},
				pluck="serial_no",
			)))
		visible_serials.update(filter(None, frappe.get_all(
			"CH Sold Plan",
			filters={"customer": customer, "docstatus": 1, "company": company_filter},
			pluck="serial_no",
		)))
		visible_serials.update(filter(None, frappe.get_all(
			"CH Warranty Claim",
			filters={"customer": customer, "docstatus": ("!=", 2), "company": company_filter},
			pluck="serial_no",
		)))

	# ── Get ALL devices for this customer ────────────────────────────
	devices = []
	seen_serials = set()

	# From CH Serial Lifecycle
	lc_devices = frappe.get_all(
		"CH Serial Lifecycle",
		filters={"customer": customer},
		fields=[
			"name as serial_no", "imei_number", "imei_number_2",
			"item_code", "item_name", "lifecycle_status",
			"sale_date", "warranty_status",
			"warranty_start_date", "warranty_end_date",
		],
		order_by="sale_date desc",
	)
	for d in lc_devices:
		d["source"] = "lifecycle"
		seen_serials.add(d["serial_no"])
		devices.append(d)

	# From CH Sold Plan (may have devices not in lifecycle)
	sp_filters = {"customer": customer, "docstatus": 1}
	if company_filter:
		sp_filters["company"] = company_filter
	sp_serials = frappe.get_all("CH Sold Plan", filters=sp_filters, pluck="serial_no")
	for sn in filter(None, sp_serials):
		if sn in seen_serials:
			continue
		# Get device info from Serial No
		sn_data = None
		if frappe.db.exists("Serial No", sn):
			sn_data = frappe.db.get_value(
				"Serial No", sn,
				["name", "item_code", "item_name", "status", "warranty_expiry_date"],
				as_dict=True,
			)
		devices.append({
			"serial_no": sn,
			"imei_number": sn,
			"imei_number_2": "",
			"item_code": (sn_data or {}).get("item_code", ""),
			"item_name": (sn_data or {}).get("item_name", ""),
			"lifecycle_status": (sn_data or {}).get("status", ""),
			"sale_date": "",
			"warranty_status": "",
			"warranty_start_date": "",
			"warranty_end_date": (sn_data or {}).get("warranty_expiry_date", ""),
			"source": "sold_plan",
		})
		seen_serials.add(sn)

	# ── For each device, get ALL sold plans + claims ─────────────────
	today = getdate(nowdate())
	filtered_devices = []
	for dev in devices:
		sn = dev["serial_no"]
		if company_scope and sn not in visible_serials:
			continue

		# Get all sold plans (Active + Expired + Claimed)
		plan_filters = {"serial_no": sn, "customer": customer, "docstatus": 1}
		if company_filter:
			plan_filters["company"] = company_filter
		all_plans = frappe.get_all(
			"CH Sold Plan",
			filters=plan_filters,
			fields=[
				"name", "warranty_plan", "plan_title", "plan_type",
				"start_date", "end_date", "claims_used", "max_claims",
				"deductible_amount", "status", "plan_price",
			],
			order_by="start_date desc",
		)

		for plan in all_plans:
			# Compute display status
			if plan.status in ("Void", "Cancelled"):
				plan["display_status"] = "void"
			elif plan.end_date and today > getdate(plan.end_date):
				plan["display_status"] = "expired"
			elif plan.max_claims and plan.max_claims > 0 and (plan.claims_used or 0) >= plan.max_claims:
				plan["display_status"] = "exhausted"
			elif plan.status == "Active":
				plan["display_status"] = "active"
			else:
				plan["display_status"] = plan.status.lower() if plan.status else "unknown"

			# Days remaining
			if plan.end_date and plan["display_status"] == "active":
				plan["days_remaining"] = (getdate(plan.end_date) - today).days
			else:
				plan["days_remaining"] = 0

			# Claims remaining
			if plan.max_claims and plan.max_claims > 0:
				plan["claims_remaining"] = max(0, plan.max_claims - (plan.claims_used or 0))
			else:
				plan["claims_remaining"] = -1  # unlimited

		dev["plans"] = all_plans
		dev["active_plans"] = [p for p in all_plans if p["display_status"] == "active"]
		dev["has_active_warranty"] = any(
			p["display_status"] == "active" for p in all_plans
		)

		# Manufacturer warranty from Serial No.warranty_expiry_date
		mfr_expiry = None
		if frappe.db.exists("Serial No", sn):
			mfr_expiry = frappe.db.get_value("Serial No", sn, "warranty_expiry_date")
		if mfr_expiry:
			dev["manufacturer_warranty_end"] = str(mfr_expiry)
			dev["manufacturer_warranty_active"] = getdate(mfr_expiry) >= today
		else:
			dev["manufacturer_warranty_end"] = ""
			dev["manufacturer_warranty_active"] = False

		# Get claims
		dev["claims"] = get_claims_for_serial(sn, company=company)
		filtered_devices.append(dev)

	devices = filtered_devices

	# ── Summary stats ────────────────────────────────────────────────
	total_plans = sum(len(d.get("plans", [])) for d in devices)
	active_plans = sum(len(d.get("active_plans", [])) for d in devices)
	total_devices = len(devices)

	# ── Unlinked plans (no serial_no) ────────────────────────────────
	unlinked_filters = {
		"customer": customer,
		"docstatus": 1,
		"serial_no": ["in", [None, ""]],
	}
	if company_filter:
		unlinked_filters["company"] = company_filter
	unlinked_plans_raw = frappe.get_all(
		"CH Sold Plan",
		filters=unlinked_filters,
		fields=[
			"name", "warranty_plan", "plan_title", "plan_type",
			"start_date", "end_date", "claims_used", "max_claims",
			"deductible_amount", "status", "plan_price",
		],
		order_by="start_date desc",
	)
	unlinked_plans = []
	for plan in unlinked_plans_raw:
		if plan.status in ("Void", "Cancelled"):
			plan["display_status"] = "void"
		elif plan.end_date and today > getdate(plan.end_date):
			plan["display_status"] = "expired"
		elif plan.max_claims and plan.max_claims > 0 and (plan.claims_used or 0) >= plan.max_claims:
			plan["display_status"] = "exhausted"
		elif plan.status == "Active":
			plan["display_status"] = "active"
		else:
			plan["display_status"] = plan.status.lower() if plan.status else "unknown"

		if plan.end_date and plan["display_status"] == "active":
			plan["days_remaining"] = (getdate(plan.end_date) - today).days
		else:
			plan["days_remaining"] = 0

		if plan.max_claims and plan.max_claims > 0:
			plan["claims_remaining"] = max(0, plan.max_claims - (plan.claims_used or 0))
		else:
			plan["claims_remaining"] = -1
		unlinked_plans.append(plan)

	total_plans += len(unlinked_plans)
	active_plans += sum(1 for p in unlinked_plans if p["display_status"] == "active")

	if company_scope and not devices and not unlinked_plans:
		return {
			"found": False,
			"search_type": search_type,
			"message": _("No warranty or claim data is available for {0}.").format(company),
			"customer": None,
			"devices": [],
		}

	return {
		"found": True,
		"search_type": search_type,
		"customer": customer,
		"customer_name": customer_name,
		"customer_phone": customer_phone,
		"customer_email": customer_email,
		"devices": devices,
		"unlinked_plans": unlinked_plans,
		"summary": {
			"total_devices": total_devices,
			"total_plans": total_plans,
			"active_plans": active_plans,
			"expired_plans": total_plans - active_plans,
		},
	}


# ── Warranty Claim APIs ─────────────────────────────────────────────────────


def _get_claim_doc(claim_name, permission_type="read"):
	"""Load a claim only after company-aware permission checks pass."""
	frappe.has_permission("CH Warranty Claim", permission_type, doc=claim_name, throw=True)
	return frappe.get_doc("CH Warranty Claim", claim_name)


@frappe.whitelist()
def initiate_warranty_claim(serial_no, customer, item_code, company,
                            issue_description, issue_category=None,
                            issue_categories=None,
                            reported_at_company=None, reported_at_store=None,
                            estimated_repair_cost=0, customer_phone=None,
							customer_email=None, sold_plan=None,
							mode_of_service="Walk-in", pickup_address=None,
							pickup_slot=None) -> dict:
	"""Initiate a new warranty claim from POS or desk.

	Auto-detects warranty coverage, calculates cost split, and either
	auto-approves (out-of-warranty) or routes for GoGizmo Head approval.

	Args:
		serial_no: Serial / IMEI of the device
		customer: Customer name
		item_code: Device item_code
		company: Company that sold the device (warranty issuer)
		issue_description: What's wrong with the device
		issue_category: GoFix Issue Category (optional, legacy)
		issue_categories: Comma-separated list of Issue Category names
		reported_at_company: Company where reported (GoGizmo or GoFix)
		reported_at_store: Store location
		estimated_repair_cost: Estimated cost of repair
		customer_phone: Customer contact number
		customer_email: Customer email address

	Returns:
		dict with: claim_name, claim_status, coverage_type, requires_approval,
		           service_request (if auto-approved)
	"""
	if not reported_at_company:
		reported_at_company = company

	frappe.has_permission("CH Warranty Claim", "create", throw=True)

	claim = frappe.new_doc("CH Warranty Claim")
	claim.update({
		"serial_no": serial_no,
		"customer": customer,
		"item_code": item_code,
		"company": company,
		"reported_at_company": reported_at_company,
		"reported_at_store": reported_at_store or "",
		"issue_description": issue_description,
		"issue_category": issue_category,
		"estimated_repair_cost": float(estimated_repair_cost or 0),
		"customer_phone": customer_phone or "",
		"customer_email": customer_email or "",
		"mode_of_service": mode_of_service or "Walk-in",
		"pickup_required": 1 if mode_of_service in ("Pickup", "Courier") else 0,
		"pickup_address": pickup_address or "",
		"pickup_slot": pickup_slot,
		"claim_date": nowdate(),
	})

	# Pre-set sold_plan if user selected a specific plan
	if sold_plan:
		claim.sold_plan = sold_plan

	# Add issue categories (Table MultiSelect)
	if issue_categories:
		import json as _json
		cats = issue_categories
		if isinstance(cats, str):
			try:
				cats = _json.loads(cats)
			except (ValueError, TypeError):
				cats = [c.strip() for c in cats.split(",") if c.strip()]
		for cat in cats:
			claim.append("issue_categories", {"issue_category": cat})

	claim.flags.ignore_permissions = True
	claim.insert()
	claim.submit()

	return {
		"claim_name": claim.name,
		"claim_status": claim.claim_status,
		"coverage_type": claim.coverage_type,
		"warranty_status": claim.warranty_status,
		"requires_approval": claim.requires_approval,
		"gogizmo_share": claim.gogizmo_share,
		"customer_share": claim.customer_share,
		"deductible_amount": claim.deductible_amount,
		"mode_of_service": claim.mode_of_service,
		"logistics_status": claim.logistics_status,
		"service_request": claim.service_request,
	}


@frappe.whitelist()
def update_claim_logistics(claim_name, action, pickup_address=None,
	                       pickup_slot=None, pickup_partner=None,
	                       pickup_tracking_no=None, delivery_otp=None,
	                       remarks=None) -> dict:
	"""Update warranty claim pickup/delivery lifecycle from POS/desk.

	Supported actions:
	- schedule_pickup
	- mark_picked_up
	- mark_out_for_delivery
	- mark_delivered_back
	"""
	if not claim_name or not action:
		frappe.throw(_("Claim name and action are required"), title=_("API Error"))

	claim = _get_claim_doc(claim_name, "write")

	if action == "schedule_pickup":
		return claim.schedule_pickup(
			pickup_address=pickup_address,
			pickup_slot=pickup_slot,
			pickup_partner=pickup_partner,
			pickup_tracking_no=pickup_tracking_no,
			remarks=remarks,
		)

	if action == "mark_picked_up":
		return claim.mark_picked_up(delivery_otp=delivery_otp, remarks=remarks)

	if action == "mark_out_for_delivery":
		return claim.mark_out_for_delivery(
			pickup_partner=pickup_partner,
			pickup_tracking_no=pickup_tracking_no,
			remarks=remarks,
		)

	if action == "mark_delivered_back":
		return claim.mark_delivered_back(delivery_otp=delivery_otp, remarks=remarks)

	frappe.throw(_("Unsupported logistics action: {0}").format(action), title=_("API Error"))


# ── Device Receiving, QC, Fee — new claim lifecycle endpoints ──────────


@frappe.whitelist()
def receive_claim_device(claim_name, condition_on_receipt=None,
                         accessories_received=None, imei_verified=0,
                         receiving_remarks=None) -> dict:
	"""Mark device as physically received at store."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.mark_device_received(
		condition_on_receipt=condition_on_receipt,
		accessories_received=accessories_received,
		imei_verified=imei_verified,
		receiving_remarks=receiving_remarks,
	)


@frappe.whitelist()
def perform_claim_qc(claim_name, qc_result, qc_remarks=None,
                     qc_result_reason=None, qc_checks=None) -> dict:
	"""Perform intake QC on received device."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.perform_intake_qc(
		qc_result=qc_result,
		qc_remarks=qc_remarks,
		qc_result_reason=qc_result_reason,
		qc_checks=qc_checks,
	)


@frappe.whitelist()
def generate_claim_fee(claim_name, fee_amount=None) -> dict:
	"""Calculate and set processing fee after QC passes."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.generate_processing_fee(fee_amount=fee_amount)


@frappe.whitelist()
def send_claim_fee_link(claim_name, channel="WhatsApp") -> dict:
	"""Send payment link for processing fee to customer."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.send_fee_payment_link(channel=channel)


@frappe.whitelist()
def mark_claim_fee_paid(claim_name, paid_amount=None, payment_mode=None,
                        payment_ref=None, remarks=None) -> dict:
	"""Record processing fee payment."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.mark_fee_paid(
		paid_amount=paid_amount,
		payment_mode=payment_mode,
		payment_ref=payment_ref,
		remarks=remarks,
	)


@frappe.whitelist()
def waive_claim_fee(claim_name, waiver_reason, waived_amount=None) -> dict:
	"""Request or approve processing fee waiver."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.waive_processing_fee(
		waiver_reason=waiver_reason,
		waived_amount=waived_amount,
	)


@frappe.whitelist()
def create_claim_repair_ticket(claim_name, remarks=None) -> dict:
	"""Create GoFix repair ticket with strict gate control."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.create_repair_ticket(remarks=remarks)


@frappe.whitelist()
def need_more_info_claim(claim_name, remarks=None) -> dict:
	"""Send claim back for more information."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.need_more_info(remarks=remarks)



@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=300, ip_based=True)
def pay_processing_fee(claim: str, amount=None) -> dict:
	"""Public endpoint for processing fee payment (via payment link).
	
	SECURITY (H4): The client-supplied `amount` parameter is IGNORED.
	Always use the claim's configured `processing_fee_amount` to prevent
	a guest from paying a reduced fee (e.g., /api/method/.../pay_processing_fee?claim=C-001&amount=1)
	"""
	if not claim or not frappe.db.exists("CH Warranty Claim", claim):
		frappe.throw(_("Invalid claim"), frappe.DoesNotExistError, title=_("API Error"))

	doc = frappe.get_doc("CH Warranty Claim", claim)

	if doc.processing_fee_status == "Paid":
		return {
			"claim_name": doc.name,
			"customer_name": doc.customer_name,
			"amount": flt(doc.processing_fee_amount),
			"status": "Paid",
			"already_paid": True,
		}

	# SECURITY (H4): ALWAYS use doc.processing_fee_amount, NEVER client amount
	fee_amount = flt(doc.processing_fee_amount)
	settings = frappe.get_cached_doc("CH VAS Settings")
	provider = settings.get("payment_gateway_provider") or ""

	if provider == "razorpay":
		return _create_razorpay_order(doc, fee_amount, settings)
	elif provider == "cashfree":
		return _create_cashfree_order(doc, fee_amount, settings)
	elif provider == "payu":
		return _create_payu_order(doc, fee_amount, settings)

	# No gateway configured — return info for manual/offline payment
	return {
		"claim_name": doc.name,
		"customer_name": doc.customer_name,
		"amount": fee_amount,
		"currency": "INR",
		"status": doc.processing_fee_status or "Pending",
		"payment_mode": "manual",
		"instructions": _("Please pay ₹{0} at the service center counter and quote claim {1}.").format(
			fee_amount, doc.name
		),
	}


def _create_razorpay_order(doc, amount: float, settings) -> dict:
	"""Create a Razorpay order and return checkout params."""
	import hmac
	import hashlib

	key_id = settings.get("payment_gateway_merchant_id")
	key_secret = settings.get_password("payment_gateway_api_key")
	if not key_id or not key_secret:
		frappe.throw(_("Razorpay credentials not configured"), title=_("Payment Config Error"))

	try:
		import requests
		payload = {
			"amount": int(amount * 100),  # paise
			"currency": "INR",
			"receipt": doc.name,
			"notes": {"claim_name": doc.name},
		}
		resp = requests.post(
			"https://api.razorpay.com/v1/orders",
			json=payload,
			auth=(key_id, key_secret),
			timeout=10,
		)
		resp.raise_for_status()
		order = resp.json()
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Razorpay order creation failed: {doc.name}")
		frappe.throw(_("Payment gateway error. Please try again."))

	frappe.db.set_value("CH Warranty Claim", doc.name, "gateway_order_id", order["id"], update_modified=False)

	return {
		"claim_name": doc.name,
		"customer_name": doc.customer_name,
		"amount": amount,
		"currency": "INR",
		"payment_mode": "razorpay",
		"key_id": key_id,
		"order_id": order["id"],
		"description": _("Processing fee for claim {0}").format(doc.name),
	}


def _create_cashfree_order(doc, amount: float, settings) -> dict:
	"""Create a Cashfree payment session."""
	app_id = settings.get("payment_gateway_merchant_id")
	secret_key = settings.get_password("payment_gateway_api_key")
	if not app_id or not secret_key:
		frappe.throw(_("Cashfree credentials not configured"), title=_("Payment Config Error"))

	try:
		import requests
		payload = {
			"order_id": doc.name,
			"order_amount": round(amount, 2),
			"order_currency": "INR",
			"customer_details": {
				"customer_id": doc.get("customer") or doc.name,
				"customer_name": doc.customer_name or "Customer",
			},
		}
		resp = requests.post(
			"https://api.cashfree.com/pg/orders",
			json=payload,
			headers={"x-api-version": "2023-08-01", "x-client-id": app_id, "x-client-secret": secret_key},
			timeout=10,
		)
		resp.raise_for_status()
		session = resp.json()
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Cashfree order creation failed: {doc.name}")
		frappe.throw(_("Payment gateway error. Please try again."))

	return {
		"claim_name": doc.name,
		"customer_name": doc.customer_name,
		"amount": amount,
		"currency": "INR",
		"payment_mode": "cashfree",
		"payment_session_id": session.get("payment_session_id"),
		"order_id": session.get("order_id"),
	}


def _create_payu_order(doc, amount: float, settings) -> dict:
	"""Build PayU payment hash."""
	import hashlib

	merchant_key = settings.get("payment_gateway_merchant_id")
	salt = settings.get_password("payment_gateway_api_key")
	if not merchant_key or not salt:
		frappe.throw(_("PayU credentials not configured"), title=_("Payment Config Error"))

	txnid = f"WC-{doc.name}"
	amount_str = f"{round(amount, 2):.2f}"
	product_info = f"Processing fee for {doc.name}"
	firstname = (doc.customer_name or "Customer").split()[0]

	# PayU hash: key|txnid|amount|productinfo|firstname|email|||||||||||salt
	hash_str = f"{merchant_key}|{txnid}|{amount_str}|{product_info}|{firstname}|customer|||||||||||{salt}"
	txn_hash = hashlib.sha512(hash_str.encode("utf-8")).hexdigest()

	return {
		"claim_name": doc.name,
		"amount": amount,
		"currency": "INR",
		"payment_mode": "payu",
		"key": merchant_key,
		"txnid": txnid,
		"hash": txn_hash,
	}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60, ip_based=True)
def payment_webhook(gateway: str = "razorpay") -> dict:
	"""Receive payment gateway callback and mark processing fee as Paid.
	
	SECURITY (H5): PayU webhooks require gateway_secret to be configured.
	Refuse callbacks if secret is not set (prevents unvalidated gateway updates).
	"""
	import hmac
	import hashlib
	import json

	raw_body = frappe.request.get_data(as_text=True)
	payload = json.loads(raw_body) if raw_body else {}
	settings = frappe.get_cached_doc("CH VAS Settings")
	webhook_secret = settings.get_password("payment_gateway_webhook_secret") or ""

	if gateway == "razorpay":
		sig_header = frappe.get_request_header("X-Razorpay-Signature") or ""
		expected = hmac.new(
			webhook_secret.encode(), raw_body.encode(), hashlib.sha256
		).hexdigest()
		if not hmac.compare_digest(expected, sig_header):
			frappe.throw(_("Webhook signature mismatch"), frappe.AuthenticationError)
		event = payload.get("event", "")
		if event == "payment.captured":
			order_id = payload.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id")
			if order_id and frappe.db.exists("CH Warranty Claim", {"gateway_order_id": order_id}):
				claim_name = frappe.db.get_value("CH Warranty Claim", {"gateway_order_id": order_id}, "name")
				frappe.db.set_value("CH Warranty Claim", claim_name, "processing_fee_status", "Paid", update_modified=True)

	elif gateway == "payu":
		# SECURITY (H5): Refuse PayU webhooks unless gateway_secret configured
		if not webhook_secret:
			frappe.log_error("PayU webhook received but gateway_secret not configured in CH VAS Settings")
			frappe.throw(_("PayU gateway not configured. Webhook rejected."), frappe.AuthenticationError)

		posted = frappe.form_dict
		status = posted.get("status", "")
		if status == "success":
			txnid = posted.get("txnid", "")
			if txnid.startswith("WC-"):
				claim_name = txnid[3:]
				if frappe.db.exists("CH Warranty Claim", claim_name):
					frappe.db.set_value("CH Warranty Claim", claim_name, "processing_fee_status", "Paid", update_modified=True)

	return {"status": "ok"}


# ── VAS Claims Dashboard (POS Claims workspace) ──────────────────────────────

@frappe.whitelist()
def get_vas_claims_dashboard(company=None, limit=15) -> dict:
	"""Aggregate VAS lifecycle data for the POS Claims workspace.

	Returns sold-plan rollup, recent claims, and voucher redemptions scoped to
	the active company. Used by claims_workspace.js → _load_vas_activity().

	Args:
		company: Company filter (defaults to active POS company / session default).
		limit: Max rows per list (sold_plans, recent_claims, voucher_redemptions).

	Returns:
		dict with keys: summary, sold_plans, recent_claims, voucher_redemptions.
	"""
	try:
		limit = int(limit or 15)
	except (TypeError, ValueError):
		limit = 15
	limit = max(1, min(limit, 100))

	company = (company or "").strip() or get_company_filter_value()

	# ── Sold plans ────────────────────────────────────────────────────────
	plan_filters = {"docstatus": 1}
	if company:
		plan_filters["company"] = company

	sold_plans = frappe.get_all(
		"CH Sold Plan",
		filters=plan_filters,
		fields=[
			"name",
			"status",
			"plan_title",
			"warranty_plan",
			"customer",
			"customer_name",
			"serial_no",
			"item_code",
			"start_date",
			"end_date",
		],
		order_by="modified desc",
		limit=limit,
	) or []

	plan_names = [p["name"] for p in sold_plans]
	claim_counts = {}
	open_counts = {}
	if plan_names:
		# CH Warranty Claim is linked to a sold plan via the `sold_plan` field
		# (fallback: by serial_no + warranty_plan). Use sold_plan when present.
		rows = frappe.db.sql(
			"""
			SELECT sold_plan,
			       COUNT(*) AS total,
			       SUM(CASE WHEN claim_status NOT IN ('Closed','Cancelled','Rejected','Delivered')
			                THEN 1 ELSE 0 END) AS open_count
			FROM `tabCH Warranty Claim`
			WHERE docstatus < 2 AND sold_plan IN %(names)s
			GROUP BY sold_plan
			""",
			{"names": tuple(plan_names)},
			as_dict=True,
		) or []
		for r in rows:
			claim_counts[r["sold_plan"]] = int(r["total"] or 0)
			open_counts[r["sold_plan"]] = int(r["open_count"] or 0)

	for p in sold_plans:
		p["claim_count"] = claim_counts.get(p["name"], 0)
		p["open_claims"] = open_counts.get(p["name"], 0)

	# ── Recent claims ─────────────────────────────────────────────────────
	claim_filters = {"docstatus": ("<", 2)}
	if company:
		# CH Warranty Claim may or may not have a company field depending on schema;
		# only filter when the column exists to avoid 1054 errors.
		if frappe.db.has_column("CH Warranty Claim", "company"):
			claim_filters["company"] = company

	recent_claims = frappe.get_all(
		"CH Warranty Claim",
		filters=claim_filters,
		fields=[
			"name",
			"claim_id",
			"claim_status",
			"customer",
			"customer_name",
			"serial_no",
			"warranty_plan",
			"creation",
			"modified",
		],
		order_by="modified desc",
		limit=limit,
	) or []

	# ── Voucher redemptions (CH VAS Ledger — claim/redeem events) ─────────
	voucher_redemptions = []
	if frappe.db.exists("DocType", "CH VAS Ledger"):
		ledger_filters = {
			"event_type": ("in", ["Claim", "Redemption", "Voucher Redemption", "Redeem"]),
		}
		voucher_redemptions = frappe.get_all(
			"CH VAS Ledger",
			filters=ledger_filters,
			fields=[
				"name",
				"sold_plan",
				"event_type",
				"claim_amount",
				"reference_doctype",
				"reference_name",
				"creation",
			],
			order_by="creation desc",
			limit=limit,
		) or []
		# Hydrate posting_date / customer for display from the linked sold plan.
		plan_lookup = {}
		need_lookup = {v["sold_plan"] for v in voucher_redemptions if v.get("sold_plan")}
		need_lookup -= set(plan_names)  # already in sold_plans payload
		if need_lookup:
			extra = frappe.get_all(
				"CH Sold Plan",
				filters={"name": ("in", list(need_lookup))},
				fields=["name", "customer", "customer_name"],
			)
			plan_lookup = {p["name"]: p for p in extra}
		for v in voucher_redemptions:
			ref = next((p for p in sold_plans if p["name"] == v.get("sold_plan")), None) or \
				plan_lookup.get(v.get("sold_plan")) or {}
			v["customer"] = ref.get("customer") or ""
			v["customer_name"] = ref.get("customer_name") or ""
			v["amount"] = flt(v.get("claim_amount") or 0)
			v["transaction_type"] = v.get("event_type") or ""
			v["posting_date"] = (v.get("creation") or "")[:10] if v.get("creation") else ""

	# ── Summary roll-up ───────────────────────────────────────────────────
	summary = {
		"total_plans": len(sold_plans),
		"active_plans": sum(1 for p in sold_plans if (p.get("status") or "").lower() == "active"),
		"total_claims": len(recent_claims),
		"open_claims": sum(
			1 for c in recent_claims
			if (c.get("claim_status") or "") not in ("Closed", "Cancelled", "Rejected", "Delivered")
		),
	}

	return {
		"summary": summary,
		"sold_plans": sold_plans,
		"recent_claims": recent_claims,
		"voucher_redemptions": voucher_redemptions,
	}
