# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Item Master — Warranty API.

Central entrypoint for warranty operations consumed by downstream apps (GoFix, etc.).
All functions are whitelisted for client/API access.

Usage from GoFix:
    from ch_item_master.ch_item_master.warranty_api import check_warranty, get_applicable_plans
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_months, cint, flt, getdate, now_datetime, nowdate

from ch_item_master.config import (
	get_enabled_role_users,
	get_int_setting,
	get_role_setting,
	has_role_setting,
	is_privileged_user,
	require_role_setting,
)
from ch_item_master.security import ensure_company_access, get_company_filter_value, get_company_scope


_WARRANTY_DASHBOARD_ROLES = (
	"CH Warranty Manager",
	"Service Manager",
	"Sales Manager",
	"Sales User",
)


def _require_warranty_dashboard_access(company=None):
	require_role_setting(
		"warranty_dashboard_roles",
		_WARRANTY_DASHBOARD_ROLES,
		action=_("view warranty dashboards"),
	)
	frappe.has_permission("Customer", "read", throw=True)
	frappe.has_permission("CH Warranty Claim", "read", throw=True)
	company_scope = get_company_scope(requested_company=company)
	if company_scope == []:
		frappe.throw(_("No company scope is assigned to your user."), frappe.PermissionError)
	if is_privileged_user():
		return company_scope
	try:
		from ch_erp15.ch_erp15.scope import get_user_scope
	except (ImportError, ModuleNotFoundError):
		frappe.throw(_("Location scope validation is unavailable."), frappe.PermissionError)
	scope = get_user_scope() or {}
	if scope.get("bypass"):
		return company_scope
	allowed_stores = set(scope.get("stores") or ())
	for scoped_company in company_scope or ():
		company_stores = set(frappe.get_all(
			"CH Store",
			filters={"company": scoped_company, "disabled": 0},
			pluck="name",
		))
		if not company_stores or not company_stores.issubset(allowed_stores):
			frappe.throw(
				_("Warranty network dashboards require full store scope for {0}.").format(scoped_company),
				frappe.PermissionError,
			)
	return company_scope


def _processing_fee_link_secret(settings=None) -> str:
	settings = settings or frappe.get_cached_doc("CH VAS Settings")
	secret = settings.get_password("processing_fee_link_secret") or ""
	if not secret:
		frappe.throw(
			_("Processing Fee Link Secret is not configured in CH VAS Settings."),
			frappe.AuthenticationError,
		)
	return secret


def _processing_fee_link_signature(claim: str, expires: int, secret: str) -> str:
	message = f"{claim}:{expires}".encode("utf-8")
	return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_processing_fee_link(claim: str) -> str:
	settings = frappe.get_cached_doc("CH VAS Settings")
	secret = _processing_fee_link_secret(settings)
	ttl_hours = max(cint(settings.get("processing_fee_link_ttl_hours")) or 72, 1)
	expires = int(time.time()) + ttl_hours * 3600
	params = {
		"claim": claim,
		"expires": expires,
		"token": _processing_fee_link_signature(claim, expires, secret),
	}
	return (
		f"{frappe.utils.get_url()}/api/method/"
		"ch_item_master.ch_item_master.warranty_api.pay_processing_fee?"
		f"{urlencode(params)}"
	)


def _validate_processing_fee_link(claim: str, expires, token: str) -> None:
	try:
		expires = int(expires)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid or expired processing-fee link."), frappe.AuthenticationError)
	if expires < int(time.time()):
		frappe.throw(_("This processing-fee link has expired."), frappe.AuthenticationError)
	secret = _processing_fee_link_secret()
	expected = _processing_fee_link_signature(claim, expires, secret)
	if not token or not hmac.compare_digest(expected, str(token)):
		frappe.throw(_("Invalid processing-fee link."), frappe.AuthenticationError)


# ── Item default (base) warranty ────────────────────────────────────────────

def get_item_default_warranty(item_code) -> dict:
	"""Canonical base-warranty profile for an Item.

	Reads the Item's default-warranty fields (type + duration + unit) and
	normalises the duration to months. Returns
	``{"type", "duration", "uom", "months", "label"}`` — months = 0 means the
	item carries no default warranty.
	"""
	row = frappe.db.get_value(
		"Item", item_code,
		["ch_default_warranty_type", "ch_default_warranty_months", "ch_default_warranty_uom"],
		as_dict=True,
	) or frappe._dict()
	duration = cint(row.get("ch_default_warranty_months"))
	uom = row.get("ch_default_warranty_uom") or "Months"
	months = duration * 12 if uom == "Years" else duration
	wtype = row.get("ch_default_warranty_type") or ("Manufacturer" if months else "")
	label = ""
	if months:
		if uom == "Years":
			label = _("{0} Warranty — {1} Year(s)").format(_(wtype), duration)
		else:
			label = _("{0} Warranty — {1} Month(s)").format(_(wtype), duration)
	return {"type": wtype, "duration": duration, "uom": uom, "months": months, "label": label}


def get_base_warranty_expiry(item_code, sale_date) -> str | None:
	"""Base-warranty expiry date for a device of `item_code` sold on `sale_date`."""
	months = get_item_default_warranty(item_code)["months"]
	if not months or not sale_date:
		return None
	return add_months(getdate(sale_date), months)


def get_invoice_warranty_rows(doc) -> list[dict]:
	"""Warranty summary rows for a Sales Invoice — used by the Custom Sales
	Invoice print format (registered as a jinja method).

	One row per serialised device on the invoice: the item's base warranty
	(type + duration + expiry from posting date) plus any Active VAS Plans
	(extended warranty / VAS) sold on this invoice for that serial.
	"""
	rows = []
	plans_by_serial = {}
	try:
		for p in frappe.get_all(
			"Active VAS Plans",
			filters={"sales_invoice": doc.name, "status": ["!=", "Void"]},
			fields=["serial_no", "plan_title", "warranty_plan", "plan_type",
			        "start_date", "end_date", "duration_months"],
		):
			plans_by_serial.setdefault(p.serial_no or "", []).append(p)
	except Exception:
		pass

	for item in (doc.items or []):
		serials = []
		if item.get("serial_no"):
			serials = [s.strip() for s in str(item.serial_no).replace(",", "\n").split("\n") if s.strip()]
		if not serials:
			# serial bundle (v15) — resolve from bundle if present
			if item.get("serial_and_batch_bundle"):
				serials = frappe.get_all(
					"Serial and Batch Entry",
					filters={"parent": item.serial_and_batch_bundle},
					pluck="serial_no",
				)
		base = get_item_default_warranty(item.item_code)
		for sn in (serials or [None]):
			if not base["months"] and not plans_by_serial.get(sn or ""):
				continue
			row = {
				"item_code": item.item_code,
				"item_name": item.item_name,
				"serial_no": sn or "",
				"base_label": base["label"],
				"base_type": base["type"],
				"base_months": base["months"],
				"base_expiry": get_base_warranty_expiry(item.item_code, doc.posting_date),
				"plans": [
					{
						"title": p.plan_title or p.warranty_plan,
						"plan_type": p.plan_type,
						"start_date": p.start_date,
						"end_date": p.end_date,
						"duration_months": p.duration_months,
					}
					for p in plans_by_serial.get(sn or "", [])
				],
			}
			rows.append(row)
	return rows


def _load_active_plan_benefits(active_plan) -> list[dict]:
	"""Return benefit rules from the Active VAS Plan snapshot."""
	raw = getattr(active_plan, "benefit_snapshot_json", None)
	if not raw:
		return []
	try:
		data = json.loads(raw)
	except Exception:
		return []
	return data if isinstance(data, list) else []


# ── Warranty Lookup ──────────────────────────────────────────────────────────

@frappe.whitelist()
def check_warranty(serial_no, company=None) -> dict:
	"""Check warranty status for a device serial/IMEI.

	Looks up Active VAS Plans records (submitted, active) for the serial.
	Falls back to CH Serial Lifecycle warranty fields if no Active VAS Plan exists.

	Args:
		serial_no: Serial number or IMEI to look up.
		company: Optional company filter.

	Returns:
		dict with: warranty_covered, warranty_status, covering_plan, all_plans,
		           serial_lifecycle (if exists), deductible_amount
	"""
	from ch_item_master.ch_item_master.doctype.active_vas_plans.active_vas_plans import (
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
		# Serial not found — check if plan allows external device
		plan = frappe.db.get_value(
			"CH Warranty Plan",
			warranty_plan,
			["allow_external_device"],
			as_dict=True,
		)
		if plan and plan.allow_external_device:
			return {
				"valid": True,
				"item_code": None,
				"category": None,
				"external_device": True,
				"message": _("Serial not found in system. This plan permits customer-provided IMEI."),
			}
		
		return {
			"valid": False,
			"item_code": None,
			"category": None,
			"external_device": False,
			"message": _("This plan cannot be sold for an IMEI not found in GoGizmo inventory."),
		}

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

@frappe.whitelist(methods=["POST"])
def issue_warranty_plan(warranty_plan, customer, item_code, serial_no=None,
                        start_date=None, company=None, sales_invoice=None,
                        sales_order=None, plan_price=None,
                        external_device_source=None,
                        is_external_device=None) -> dict:
	"""Issue (create + submit) a Active VAS Plan for a customer/device.

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
		external_device_source: Free-text source label when the device is a
			customer-provided IMEI outside GoGizmo inventory (e.g.
			"POS Customer-Provided IMEI"). Sets is_external_device=1
			implicitly.
		is_external_device: Explicit override for the is_external_device
			flag. Defaults to truthy when external_device_source is set.

	Returns:
		dict with: active_plan name, status
	"""
	frappe.has_permission("Active VAS Plans", "create", throw=True)

	plan = frappe.get_doc("CH Warranty Plan", warranty_plan)
	plan.check_permission("read")

	if not start_date:
		start_date = nowdate()
		if cint(plan.get("starts_after_base_warranty")):
			# Stack on the device's base (manufacturer/seller) warranty:
			# coverage begins the day after the base warranty expires.
			base_expiry = None
			if serial_no:
				base_expiry = frappe.db.get_value(
					"CH Customer Device", {"serial_no": serial_no}, "base_warranty_expiry")
			if not base_expiry and item_code:
				base_expiry = get_base_warranty_expiry(item_code, start_date)
			if base_expiry and getdate(base_expiry) >= getdate(start_date):
				start_date = frappe.utils.add_days(base_expiry, 1)

	if not company:
		company = plan.company
	if not company:
		frappe.throw(_("Company is required to issue a warranty plan."), frappe.ValidationError)
	ensure_company_access(company)
	if plan.company and plan.company != company:
		frappe.throw(_("The warranty plan belongs to another company."), frappe.PermissionError)
	frappe.has_permission("Customer", "read", customer, throw=True)
	frappe.has_permission("Item", "read", item_code, throw=True)
	for doctype, name in (("Sales Invoice", sales_invoice), ("Sales Order", sales_order)):
		if not name:
			continue
		linked = frappe.get_doc(doctype, name)
		linked.check_permission("read")
		if linked.company != company:
			frappe.throw(_("{0} {1} belongs to another company.").format(doctype, name), frappe.PermissionError)

	# Calculate end_date
	end_date = None
	if plan.duration_months:
		end_date = add_months(start_date, plan.duration_months)

	# Auto-flag external device when a source label is supplied.
	if external_device_source and is_external_device is None:
		is_external_device = 1

	doc = frappe.new_doc("Active VAS Plans")
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
		"external_device_source": external_device_source,
		"is_external_device": 1 if is_external_device else 0,
		"plan_price": plan_price or plan.price,
		"max_claims": plan.max_claims or 0,
		"deductible_amount": plan.deductible_amount or 0,
		"claims_per_year": plan.claims_per_year or 0,
		"fulfillment_type": plan.fulfillment_type or "Repair Claim",
	})

	doc.insert()
	doc.submit()

	return {
		"active_plan": doc.name,
		"sold_plan": doc.name,
		"status": doc.status,
		"end_date": str(doc.end_date),
		"fulfillment_type": doc.fulfillment_type,
	}


# ── Claim Recording ─────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
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
	from ch_item_master.ch_item_master.doctype.active_vas_plans.active_vas_plans import (
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
	doc = frappe.get_doc("Active VAS Plans", best_plan["name"])
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
	"""Pre-validate whether a claim is eligible under a active VAS plan.

	Checks expiry, claim count limits, annual limits, value caps,
	and per-issue coverage rules (from CH Coverage Rule child table).

	Args:
		sold_plan_name: Active VAS Plans name
		issue_type: Issue Category name (optional — for coverage rule lookup)
		estimate_amount: Estimated repair cost

	Returns:
		dict with: eligible (bool), covered_amount, customer_payable,
		           deductible, coverage_percent, reason
	"""
	estimate_amount = flt(estimate_amount)

	if not frappe.db.exists("Active VAS Plans", sold_plan_name):
		return {"eligible": False, "reason": _("Active VAS Plan {0} not found").format(sold_plan_name)}

	sp = frappe.get_doc("Active VAS Plans", sold_plan_name)

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
	benefit_match = None

	if issue_type:
		for benefit in _load_active_plan_benefits(sp):
			if benefit.get("issue_type") == issue_type:
				benefit_match = benefit
				break

		if benefit_match:
			if not int(flt(benefit_match.get("covered", 1))):
				return {"eligible": False,
				        "reason": _("Issue type '{0}' is not covered under this active plan benefit").format(issue_type)}
			coverage_percent = flt(benefit_match.get("coverage_percent")) or coverage_percent
			if benefit_match.get("deductible_amount") is not None:
				deductible = flt(benefit_match.get("deductible_amount"))

	if issue_type and sp.warranty_plan and not benefit_match:
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

	if benefit_match and flt(benefit_match.get("value_limit")) > 0:
		benefit_limit = flt(benefit_match.get("value_limit"))
		if covered_amount > benefit_limit:
			covered_amount = benefit_limit
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
			"benefit_match": benefit_match.get("benefit_code") if benefit_match else None,
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

def expire_sold_plans(batch_limit=None):
	"""Expire one bounded batch of sold plans after writing their audit events."""
	today_date = nowdate()
	batch_limit = min(
		cint(batch_limit) or get_int_setting("scheduler_batch_limit", 500, minimum=1),
		5000,
	)
	candidates = frappe.get_all(
		"Active VAS Plans",
		filters={
			"status": "Active",
			"docstatus": 1,
			"end_date": ("<", today_date),
		},
		pluck="name",
		order_by="end_date asc, name asc",
		limit=batch_limit + 1,
	)
	candidates_to_process = candidates[:batch_limit]
	if not candidates_to_process:
		return {"expired": 0, "failed": 0, "has_more": False}

	from ch_item_master.ch_item_master.doctype.ch_vas_ledger.ch_vas_ledger import log_vas_event

	successful = []
	failed = 0
	for index, name in enumerate(candidates_to_process):
		save_point = f"vas_plan_expiry_{index}"
		frappe.db.savepoint(save_point)
		try:
			log_vas_event(
				sold_plan=name,
				event_type="Plan Expired",
				remarks="Auto-expired by scheduled task",
			)
			successful.append(name)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			failed += 1
			frappe.log_error(
				frappe.get_traceback(),
				f"VAS Ledger expiry logging failed for {name}",
			)

	if successful:
		frappe.db.sql(
			"""
				UPDATE `tabActive VAS Plans`
				SET `status` = 'Expired'
				WHERE `name` IN %(names)s
				  AND `status` = 'Active'
				  AND `docstatus` = 1
				  AND `end_date` < %(today)s
			""",
			{"names": tuple(successful), "today": today_date},
		)
		frappe.logger("ch_item_master").info(
			f"Auto-expired {len(successful)} active VAS plans"
		)
	return {
		"expired": len(successful),
		"failed": failed,
		"has_more": len(candidates) > batch_limit or bool(failed),
	}


# ── Customer Warranty Dashboard ──────────────────────────────────────────────


def _decorate_warranty_dashboard_plan(plan, today):
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

	plan["days_remaining"] = (
		(getdate(plan.end_date) - today).days
		if plan.end_date and plan["display_status"] == "active"
		else 0
	)
	plan["claims_remaining"] = (
		max(0, plan.max_claims - (plan.claims_used or 0))
		if plan.max_claims and plan.max_claims > 0
		else -1
	)
	return plan


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
	company_scope = _require_warranty_dashboard_access(company)
	frappe.has_permission("CH Serial Lifecycle", "read", throw=True)
	frappe.has_permission("Active VAS Plans", "read", throw=True)
	if frappe.db.exists("DocType", "CH Customer Device"):
		frappe.has_permission("CH Customer Device", "read", throw=True)
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

		# Fallback: check Active VAS Plans
		if not customer:
			customer = frappe.db.get_value(
				"Active VAS Plans",
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
	frappe.has_permission("Customer", "read", doc=customer, throw=True)
	cust_data = frappe.db.get_value(
		"Customer", customer,
		["name", "customer_name", "mobile_no", "ch_alternate_phone", "email_id"],
		as_dict=True,
	)
	if cust_data:
		customer_name = cust_data.customer_name
		customer_phone = cust_data.mobile_no or cust_data.ch_alternate_phone or ""
		customer_email = cust_data.email_id or ""

	device_limit = min(get_int_setting("warranty_dashboard_device_limit", 100, minimum=1), 500)

	# ── Determine which serials are visible for the requested company ─────────
	visible_serials = set()
	if company_scope:
		if frappe.db.exists("DocType", "CH Customer Device"):
			visible_serials.update(filter(None, frappe.get_all(
				"CH Customer Device",
				filters={"customer": customer, "company": company_filter},
				pluck="serial_no",
				limit_page_length=device_limit,
			)))
		visible_serials.update(filter(None, frappe.get_all(
			"Active VAS Plans",
			filters={"customer": customer, "docstatus": 1, "company": company_filter},
			pluck="serial_no",
			limit_page_length=device_limit,
		)))
		visible_serials.update(filter(None, frappe.get_all(
			"CH Warranty Claim",
			filters={"customer": customer, "docstatus": ("!=", 2), "company": company_filter},
			pluck="serial_no",
			limit_page_length=device_limit,
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
		limit_page_length=device_limit,
	)
	for d in lc_devices:
		if company_scope and d["serial_no"] not in visible_serials:
			continue
		d["source"] = "lifecycle"
		seen_serials.add(d["serial_no"])
		devices.append(d)

	# From Active VAS Plans (may have devices not in lifecycle)
	sp_filters = {"customer": customer, "docstatus": 1}
	if company_filter:
		sp_filters["company"] = company_filter
	remaining_devices = max(device_limit - len(devices), 0)
	sp_serials = (
		frappe.get_all(
			"Active VAS Plans",
			filters=sp_filters,
			pluck="serial_no",
			limit_page_length=remaining_devices,
		)
		if remaining_devices
		else []
	)
	new_serials = []
	for sn in filter(None, sp_serials):
		if sn not in seen_serials and sn not in new_serials:
			new_serials.append(sn)
		if len(new_serials) >= remaining_devices:
			break

	serial_names = tuple(dict.fromkeys([d["serial_no"] for d in devices] + new_serials))
	serial_rows = frappe.get_all(
		"Serial No",
		filters={"name": ("in", serial_names)},
		fields=["name", "item_code", "item_name", "status", "warranty_expiry_date"],
		limit_page_length=len(serial_names),
	) if serial_names else []
	serial_by_name = {row.name: row for row in serial_rows}

	for sn in new_serials:
		sn_data = serial_by_name.get(sn) or {}
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

	device_serials = tuple(d["serial_no"] for d in devices)
	plan_filters = {
		"serial_no": ("in", device_serials),
		"customer": customer,
		"docstatus": 1,
	}
	claim_filters = {"serial_no": ("in", device_serials), "docstatus": ("!=", 2)}
	if company_filter:
		plan_filters["company"] = company_filter
		claim_filters["company"] = company_filter
	plan_rows = frappe.get_all(
		"Active VAS Plans",
		filters=plan_filters,
		fields=[
			"name", "serial_no", "warranty_plan", "plan_title", "plan_type",
			"start_date", "end_date", "claims_used", "max_claims",
			"deductible_amount", "status", "plan_price",
		],
		order_by="start_date desc, name desc",
		limit_page_length=device_limit,
	) if device_serials else []
	claim_rows = frappe.get_all(
		"CH Warranty Claim",
		filters=claim_filters,
		fields=[
			"name", "serial_no", "claim_date", "claim_status", "coverage_type",
			"issue_description", "service_request", "repair_status",
			"gogizmo_share", "customer_share", "mode_of_service", "logistics_status",
		],
		order_by="creation desc, name desc",
		limit_page_length=device_limit,
	) if device_serials else []
	plans_by_serial = {}
	for plan in plan_rows:
		plans_by_serial.setdefault(plan.serial_no, []).append(plan)
	claims_by_serial = {}
	for claim in claim_rows:
		claims_by_serial.setdefault(claim.serial_no, []).append(claim)

	today = getdate(nowdate())
	for dev in devices:
		sn = dev["serial_no"]
		all_plans = plans_by_serial.get(sn, [])
		for plan in all_plans:
			_decorate_warranty_dashboard_plan(plan, today)

		dev["plans"] = all_plans
		dev["active_plans"] = [p for p in all_plans if p["display_status"] == "active"]
		dev["has_active_warranty"] = any(
			p["display_status"] == "active" for p in all_plans
		)

		# Manufacturer warranty from Serial No.warranty_expiry_date
		mfr_expiry = (serial_by_name.get(sn) or {}).get("warranty_expiry_date")
		if mfr_expiry:
			dev["manufacturer_warranty_end"] = str(mfr_expiry)
			dev["manufacturer_warranty_active"] = getdate(mfr_expiry) >= today
		else:
			dev["manufacturer_warranty_end"] = ""
			dev["manufacturer_warranty_active"] = False

		dev["claims"] = claims_by_serial.get(sn, [])

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
		"Active VAS Plans",
		filters=unlinked_filters,
		fields=[
			"name", "warranty_plan", "plan_title", "plan_type",
			"start_date", "end_date", "claims_used", "max_claims",
			"deductible_amount", "status", "plan_price",
		],
		order_by="start_date desc",
		limit_page_length=device_limit,
	)
	unlinked_plans = []
	for plan in unlinked_plans_raw:
		unlinked_plans.append(_decorate_warranty_dashboard_plan(plan, today))

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
def get_claim_ui_capabilities(claim_name) -> dict:
	"""Return claim actions from configured roles and named record permission."""
	claim = _get_claim_doc(claim_name, "read")
	can_write = bool(
		is_privileged_user()
		or frappe.has_permission("CH Warranty Claim", "write", doc=claim, throw=False)
	)
	return {
		"can_perform_intake_qc": bool(
			can_write
			and claim.docstatus == 1
			and claim.claim_status in ("Device Received", "QC Pending")
			and has_role_setting(
				"warranty_claim_qc_roles",
				("CH Warranty Manager", "Service Manager", "Store Manager", "Stock Manager"),
			)
		)
	}


@frappe.whitelist(methods=["POST"])
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

	require_role_setting(
		"warranty_claim_intake_roles",
		_WARRANTY_DASHBOARD_ROLES,
		action=_("create warranty claims"),
	)
	frappe.has_permission("CH Warranty Claim", "create", throw=True)
	frappe.has_permission("CH Warranty Claim", "submit", throw=True)
	frappe.has_permission("Customer", "read", doc=customer, throw=True)
	frappe.has_permission("Item", "read", doc=item_code, throw=True)
	ensure_company_access(company)
	ensure_company_access(reported_at_company)
	if not is_privileged_user():
		try:
			from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
		except (ImportError, ModuleNotFoundError):
			frappe.throw(_("Location scope validation is unavailable."), frappe.PermissionError)
		assert_user_has_store_scope(store=reported_at_store, company=reported_at_company)

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


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
def generate_claim_fee(claim_name, fee_amount=None) -> dict:
	"""Calculate and set processing fee after QC passes."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.generate_processing_fee(fee_amount=fee_amount)


@frappe.whitelist(methods=["POST"])
def send_claim_fee_link(claim_name, channel="WhatsApp") -> dict:
	"""Send payment link for processing fee to customer."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.send_fee_payment_link(channel=channel)


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
def waive_claim_fee(claim_name, waiver_reason, waived_amount=None) -> dict:
	"""Request or approve processing fee waiver."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.waive_processing_fee(
		waiver_reason=waiver_reason,
		waived_amount=waived_amount,
	)


@frappe.whitelist(methods=["POST"])
def create_claim_repair_ticket(claim_name, remarks=None) -> dict:
	"""Create GoFix repair ticket with strict gate control."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.create_repair_ticket(remarks=remarks)


@frappe.whitelist(methods=["POST"])
def need_more_info_claim(claim_name, remarks=None) -> dict:
	"""Send claim back for more information."""
	claim = _get_claim_doc(claim_name, "write")
	return claim.need_more_info(remarks=remarks)



@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=300, ip_based=True)
def pay_processing_fee(claim: str, token=None, expires=None, amount=None) -> dict:
	"""Public endpoint for processing fee payment (via payment link).
	
	SECURITY (H4): The client-supplied `amount` parameter is IGNORED.
	Always use the claim's configured `processing_fee_amount` to prevent
	a guest from paying a reduced fee (e.g., /api/method/.../pay_processing_fee?claim=C-001&amount=1)
	"""
	_validate_processing_fee_link(claim, expires, token)
	if not claim or not frappe.db.exists("CH Warranty Claim", claim):
		frappe.throw(_("Invalid claim"), frappe.DoesNotExistError, title=_("API Error"))

	frappe.db.sql("SELECT name FROM `tabCH Warranty Claim` WHERE name = %s FOR UPDATE", (claim,))
	doc = frappe.get_doc("CH Warranty Claim", claim)

	if doc.processing_fee_status == "Paid":
		return {
			"claim_name": doc.name,
			"customer_name": doc.customer_name,
			"amount": flt(doc.processing_fee_amount),
			"status": "Paid",
			"already_paid": True,
		}
	if doc.processing_fee_status not in ("Pending", "Link Sent"):
		frappe.throw(_("This processing fee is not payable."), frappe.ValidationError)

	# SECURITY (H4): ALWAYS use doc.processing_fee_amount, NEVER client amount
	fee_amount = flt(doc.processing_fee_amount)
	if fee_amount <= 0:
		frappe.throw(_("This claim has no processing fee due."), frappe.ValidationError)
	settings = frappe.get_cached_doc("CH VAS Settings")
	provider = (settings.get("payment_gateway_provider") or "").strip().lower()
	currency = settings.get("payment_currency") or "INR"
	if provider in {"razorpay", "cashfree", "payu"}:
		attempt = _get_or_create_payment_attempt(doc, provider, fee_amount, currency)

	if provider == "razorpay":
		return _create_razorpay_order(doc, attempt, fee_amount, settings)
	elif provider == "cashfree":
		return _create_cashfree_order(doc, attempt, fee_amount, settings)
	elif provider == "payu":
		return _create_payu_order(doc, attempt, fee_amount, settings)

	# No gateway configured — return info for manual/offline payment
	return {
		"claim_name": doc.name,
		"customer_name": doc.customer_name,
		"amount": fee_amount,
		"currency": settings.get("payment_currency") or "INR",
		"status": doc.processing_fee_status or "Pending",
		"payment_mode": "manual",
		"instructions": _("Please pay ₹{0} at the service center counter and quote claim {1}.").format(
			fee_amount, doc.name
		),
	}


def _get_or_create_payment_attempt(doc, provider: str, amount: float, currency: str):
	attempt_name = frappe.db.get_value(
		"CH Warranty Payment Attempt",
		{
			"warranty_claim": doc.name,
			"provider": provider,
			"amount": flt(amount, 2),
			"currency": currency,
			"status": ("in", ("Created", "Pending", "Captured")),
		},
		"name",
		order_by="creation desc",
		for_update=True,
	)
	if attempt_name:
		return frappe.get_doc("CH Warranty Payment Attempt", attempt_name)

	attempt = frappe.get_doc({
		"doctype": "CH Warranty Payment Attempt",
		"warranty_claim": doc.name,
		"provider": provider,
		"amount": flt(amount, 2),
		"currency": currency,
		"status": "Created",
		"created_at": now_datetime(),
	})
	attempt.insert(ignore_permissions=True)
	attempt.db_set("merchant_request_id", attempt.name, update_modified=False)
	attempt.merchant_request_id = attempt.name
	return attempt


def _payment_attempt_failure(doc, attempt, provider: str) -> dict:
	return {
		"claim_name": doc.name,
		"customer_name": doc.customer_name,
		"amount": flt(attempt.amount),
		"currency": attempt.currency,
		"payment_mode": provider,
		"status": "failed",
		"retryable": True,
		"message": _("Payment gateway error. Please try again."),
	}


def _create_razorpay_order(doc, attempt, amount: float, settings) -> dict:
	"""Create a Razorpay order and return checkout params."""
	from ch_item_master.outbound_security import parse_exact_host_allowlist, post_json_with_credentials

	key_id = settings.get("payment_gateway_merchant_id")
	key_secret = settings.get_password("payment_gateway_api_key")
	order_api_url = settings.get("razorpay_order_api_url")
	timeout_seconds = cint(settings.get("gateway_timeout_seconds")) or 10
	max_response_bytes = cint(settings.get("gateway_response_max_bytes")) or 65536
	allowed_hosts = parse_exact_host_allowlist(
		settings.get("razorpay_allowed_hosts") or "api.razorpay.com",
		label="Razorpay",
	)
	currency = settings.get("payment_currency") or "INR"
	if not key_id or not key_secret:
		frappe.throw(_("Razorpay credentials not configured"), title=_("Payment Config Error"))
	if not order_api_url:
		frappe.throw(_("Razorpay Order API URL is not configured"), title=_("Payment Config Error"))
	if attempt.provider_order_id:
		return {
			"claim_name": doc.name,
			"customer_name": doc.customer_name,
			"amount": amount,
			"currency": currency,
			"payment_mode": "razorpay",
			"key_id": key_id,
			"order_id": attempt.provider_order_id,
			"description": _("Processing fee for claim {0}").format(doc.name),
			"reused": True,
		}

	try:
		payload = {
			"amount": int(amount * 100),
			"currency": currency,
			"receipt": attempt.merchant_request_id,
			"notes": {"claim_name": doc.name, "payment_attempt": attempt.name},
		}
		order = post_json_with_credentials(
			order_api_url,
			allowed_hosts=allowed_hosts,
			label="Razorpay Order API",
			payload=payload,
			auth=(key_id, key_secret),
			timeout=timeout_seconds,
			max_response_bytes=max_response_bytes,
		)
		order_id = (order.get("id") or "").strip()
		if not order_id:
			raise ValueError("Razorpay response did not include an order ID")
	except Exception as exc:
		attempt.db_set({"status": "Failed", "last_error": str(exc)[:500]}, update_modified=False)
		frappe.log_error(frappe.get_traceback(), f"Razorpay order creation failed: {doc.name}")
		return _payment_attempt_failure(doc, attempt, "razorpay")

	attempt.db_set({"provider_order_id": order_id, "status": "Pending", "last_error": ""}, update_modified=False)

	return {
		"claim_name": doc.name,
		"customer_name": doc.customer_name,
		"amount": amount,
		"currency": currency,
		"payment_mode": "razorpay",
		"key_id": key_id,
		"order_id": order_id,
		"description": _("Processing fee for claim {0}").format(doc.name),
	}


def _create_cashfree_order(doc, attempt, amount: float, settings) -> dict:
	"""Create a Cashfree payment session."""
	from ch_item_master.outbound_security import parse_exact_host_allowlist, post_json_with_credentials

	app_id = settings.get("payment_gateway_merchant_id")
	secret_key = settings.get_password("payment_gateway_api_key")
	order_api_url = settings.get("cashfree_order_api_url")
	timeout_seconds = cint(settings.get("gateway_timeout_seconds")) or 10
	max_response_bytes = cint(settings.get("gateway_response_max_bytes")) or 65536
	allowed_hosts = parse_exact_host_allowlist(
		settings.get("cashfree_allowed_hosts") or "api.cashfree.com\nsandbox.cashfree.com",
		label="Cashfree",
	)
	currency = settings.get("payment_currency") or "INR"
	if not app_id or not secret_key:
		frappe.throw(_("Cashfree credentials not configured"), title=_("Payment Config Error"))
	if not order_api_url:
		frappe.throw(_("Cashfree Order API URL is not configured"), title=_("Payment Config Error"))
	if attempt.provider_order_id and attempt.payment_session_id:
		return {
			"claim_name": doc.name,
			"customer_name": doc.customer_name,
			"amount": amount,
			"currency": currency,
			"payment_mode": "cashfree",
			"payment_session_id": attempt.payment_session_id,
			"order_id": attempt.provider_order_id,
			"reused": True,
		}

	try:
		payload = {
			"order_id": attempt.merchant_request_id,
			"order_amount": round(amount, 2),
			"order_currency": currency,
			"customer_details": {
				"customer_id": doc.get("customer") or doc.name,
				"customer_name": doc.customer_name or "Customer",
			},
		}
		session = post_json_with_credentials(
			order_api_url,
			allowed_hosts=allowed_hosts,
			label="Cashfree Order API",
			payload=payload,
			headers={"x-api-version": "2023-08-01", "x-client-id": app_id, "x-client-secret": secret_key},
			timeout=timeout_seconds,
			max_response_bytes=max_response_bytes,
		)
		order_id = (session.get("order_id") or attempt.merchant_request_id).strip()
		payment_session_id = (session.get("payment_session_id") or "").strip()
		if not order_id or not payment_session_id:
			raise ValueError("Cashfree response did not include the payment session")
	except Exception as exc:
		attempt.db_set({"status": "Failed", "last_error": str(exc)[:500]}, update_modified=False)
		frappe.log_error(frappe.get_traceback(), f"Cashfree order creation failed: {doc.name}")
		return _payment_attempt_failure(doc, attempt, "cashfree")

	attempt.db_set({
		"provider_order_id": order_id,
		"payment_session_id": payment_session_id,
		"status": "Pending",
		"last_error": "",
	}, update_modified=False)

	return {
		"claim_name": doc.name,
		"customer_name": doc.customer_name,
		"amount": amount,
		"currency": currency,
		"payment_mode": "cashfree",
		"payment_session_id": payment_session_id,
		"order_id": order_id,
	}


def _create_payu_order(doc, attempt, amount: float, settings) -> dict:
	"""Build PayU payment hash."""
	import hashlib

	merchant_key = settings.get("payment_gateway_merchant_id")
	salt = settings.get_password("payment_gateway_api_key")
	if not merchant_key or not salt:
		frappe.throw(_("PayU credentials not configured"), title=_("Payment Config Error"))

	txnid = attempt.provider_order_id or attempt.merchant_request_id
	amount_str = f"{round(amount, 2):.2f}"
	product_info = f"Processing fee for {doc.name}"
	firstname = (doc.customer_name or "Customer").split()[0]

	# PayU hash: key|txnid|amount|productinfo|firstname|email|||||||||||salt
	hash_str = f"{merchant_key}|{txnid}|{amount_str}|{product_info}|{firstname}|customer|||||||||||{salt}"
	txn_hash = hashlib.sha512(hash_str.encode("utf-8")).hexdigest()
	if not attempt.provider_order_id:
		attempt.db_set({"provider_order_id": txnid, "status": "Pending", "last_error": ""}, update_modified=False)

	return {
		"claim_name": doc.name,
		"amount": amount,
		"currency": settings.get("payment_currency") or "INR",
		"payment_mode": "payu",
		"key": merchant_key,
		"txnid": txnid,
		"hash": txn_hash,
	}


def _require_webhook_secret(settings) -> str:
	secret = settings.get_password("payment_gateway_webhook_secret") or ""
	if not secret:
		frappe.throw(_("Payment gateway webhook secret is not configured."), frappe.AuthenticationError)
	return secret


def _attempt_for_gateway_order(provider: str, order_id: str, merchant_request_id: str | None = None):
	attempt_name = frappe.db.get_value(
		"CH Warranty Payment Attempt",
		{"provider": provider, "provider_order_id": order_id},
		"name",
		for_update=True,
	)
	if not attempt_name and merchant_request_id:
		attempt_name = frappe.db.get_value(
			"CH Warranty Payment Attempt",
			{"provider": provider, "merchant_request_id": merchant_request_id},
			"name",
			for_update=True,
		)
	if not attempt_name:
		frappe.throw(_("Unknown payment gateway order."), frappe.AuthenticationError)
	attempt = frappe.get_doc("CH Warranty Payment Attempt", attempt_name)
	if attempt.provider_order_id and attempt.provider_order_id != order_id:
		frappe.throw(_("Payment attempt is bound to another provider order."), frappe.AuthenticationError)
	if not attempt.provider_order_id:
		attempt.db_set({"provider_order_id": order_id, "status": "Pending"}, update_modified=False)
		attempt.provider_order_id = order_id
	return attempt


def _settle_processing_fee(attempt, amount, payment_mode: str, payment_ref: str, payload) -> dict:
	claim_name = attempt.warranty_claim
	payment_ref = (payment_ref or "").strip()
	if not payment_ref:
		frappe.throw(_("Payment gateway callback is missing the provider payment ID."), frappe.AuthenticationError)
	if attempt.status == "Settled":
		if payment_ref != (attempt.provider_payment_id or ""):
			frappe.throw(_("Payment attempt was already settled by another provider payment."), frappe.AuthenticationError)
		return {"status": "settled", "already_settled": True}
	if attempt.provider_payment_id and payment_ref != attempt.provider_payment_id:
		frappe.throw(_("Payment attempt is already bound to another provider payment."), frappe.AuthenticationError)
	duplicate = frappe.db.get_value(
		"CH Warranty Payment Attempt",
		{"provider_payment_id": payment_ref, "name": ("!=", attempt.name)},
		"name",
		for_update=True,
	)
	if duplicate:
		frappe.throw(_("Provider payment ID is already linked to another attempt."), frappe.AuthenticationError)

	frappe.db.sql("SELECT name FROM `tabCH Warranty Claim` WHERE name = %s FOR UPDATE", (claim_name,))
	doc = frappe.get_doc("CH Warranty Claim", claim_name)
	if doc.processing_fee_status == "Paid":
		if payment_ref != (doc.processing_fee_payment_ref or ""):
			frappe.throw(_("Claim was already settled by another provider payment."), frappe.AuthenticationError)
		attempt.db_set({
			"provider_payment_id": payment_ref,
			"status": "Settled",
			"settled_at": now_datetime(),
			"provider_payload": json.dumps(payload, default=str)[:10000],
			"last_error": "",
		}, update_modified=False)
		return {"status": "settled", "already_settled": True}
	if doc.processing_fee_status not in ("Pending", "Link Sent", "Posting Pending", "Posting Failed"):
		frappe.throw(_("Processing fee is not payable for this claim."), frappe.ValidationError)
	expected_amount = flt(doc.processing_fee_amount, 2)
	if abs(flt(attempt.amount, 2) - expected_amount) > 0.01 or abs(flt(amount, 2) - expected_amount) > 0.01:
		frappe.throw(_("Payment amount does not match the claim fee."), frappe.AuthenticationError)
	attempt.db_set({
		"provider_payment_id": payment_ref,
		"status": "Captured",
		"provider_payload": json.dumps(payload, default=str)[:10000],
		"last_error": "",
	}, update_modified=False)
	result = doc._mark_fee_paid(
		paid_amount=expected_amount,
		payment_mode=payment_mode,
		payment_ref=payment_ref,
		remarks=_("Verified {0} gateway callback").format(payment_mode),
	)
	if (result or {}).get("processing_fee_status") == "Paid":
		attempt.db_set({
			"status": "Settled",
			"settled_at": now_datetime(),
			"last_error": "",
		}, update_modified=False)
		return {"status": "settled"}
	attempt.db_set("last_error", doc.get("processing_fee_gl_error") or _("Accounting posting failed."), update_modified=False)
	return {"status": "captured", "accounting_retry_required": True}


def _verify_payu_hash(posted, settings) -> None:
	merchant_key = settings.get("payment_gateway_merchant_id") or ""
	salt = settings.get_password("payment_gateway_api_key") or ""
	if not merchant_key or not salt:
		frappe.throw(_("PayU credentials are not configured."), frappe.AuthenticationError)
	sequence = (
		f"{salt}|{posted.get('status', '')}|||||||||||{posted.get('email', '')}|"
		f"{posted.get('firstname', '')}|{posted.get('productinfo', '')}|{posted.get('amount', '')}|"
		f"{posted.get('txnid', '')}|{posted.get('key', '')}"
	)
	additional_charges = posted.get("additionalCharges") or posted.get("additional_charges")
	if additional_charges:
		sequence = f"{additional_charges}|{sequence}"
	expected = hashlib.sha512(sequence.encode("utf-8")).hexdigest()
	if not posted.get("hash") or not hmac.compare_digest(expected, str(posted.get("hash"))):
		frappe.throw(_("PayU webhook hash mismatch."), frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60, ip_based=True)
def payment_webhook(gateway: str = "razorpay") -> dict:
	"""Verify a gateway callback before settling the linked processing fee."""
	gateway = (gateway or "").strip().lower()
	settings = frappe.get_cached_doc("CH VAS Settings")
	configured_gateway = (settings.get("payment_gateway_provider") or "").strip().lower()
	if not configured_gateway or gateway != configured_gateway:
		frappe.throw(_("Payment gateway is not enabled for this callback."), frappe.AuthenticationError)

	raw_body = frappe.request.get_data(cache=True) or b""
	raw_text = raw_body.decode("utf-8")

	if gateway == "razorpay":
		secret = _require_webhook_secret(settings)
		signature = frappe.get_request_header("X-Razorpay-Signature") or ""
		expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
		if not signature or not hmac.compare_digest(expected, signature):
			frappe.throw(_("Razorpay webhook signature mismatch."), frappe.AuthenticationError)
		payload = json.loads(raw_text or "{}")
		if payload.get("event") != "payment.captured":
			return {"status": "ignored"}
		payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
		order_id = payment.get("order_id") or ""
		notes = payment.get("notes") or {}
		if not isinstance(notes, dict):
			notes = {}
		attempt = _attempt_for_gateway_order(
			"razorpay",
			order_id,
			notes.get("payment_attempt"),
		)
		settlement = _settle_processing_fee(
			attempt,
			flt(payment.get("amount")) / 100,
			"Razorpay",
			payment.get("id"),
			payload,
		)

	elif gateway == "cashfree":
		secret = _require_webhook_secret(settings)
		timestamp = frappe.get_request_header("X-Webhook-Timestamp") or ""
		signature = frappe.get_request_header("X-Webhook-Signature") or ""
		digest = hmac.new(secret.encode("utf-8"), f"{timestamp}{raw_text}".encode("utf-8"), hashlib.sha256).digest()
		expected = base64.b64encode(digest).decode("ascii")
		if not timestamp or not signature or not hmac.compare_digest(expected, signature):
			frappe.throw(_("Cashfree webhook signature mismatch."), frappe.AuthenticationError)
		payload = json.loads(raw_text or "{}")
		data = payload.get("data", {})
		payment = data.get("payment", {})
		if payment.get("payment_status") != "SUCCESS":
			return {"status": "ignored"}
		order = data.get("order", {})
		order_id = order.get("order_id") or ""
		attempt = _attempt_for_gateway_order("cashfree", order_id, order_id)
		settlement = _settle_processing_fee(
			attempt,
			payment.get("payment_amount") or order.get("order_amount"),
			"Cashfree",
			payment.get("cf_payment_id"),
			payload,
		)

	elif gateway == "payu":
		posted = dict(frappe.form_dict)
		_verify_payu_hash(posted, settings)
		if posted.get("status") != "success":
			return {"status": "ignored"}
		txnid = posted.get("txnid") or ""
		attempt = _attempt_for_gateway_order("payu", txnid, txnid)
		claim_name = attempt.warranty_claim
		doc = frappe.get_doc("CH Warranty Claim", claim_name)
		expected_fields = {
			"key": settings.get("payment_gateway_merchant_id") or "",
			"productinfo": f"Processing fee for {claim_name}",
			"firstname": (doc.customer_name or "Customer").split()[0],
			"email": "customer",
		}
		if any(str(posted.get(key) or "") != str(value) for key, value in expected_fields.items()):
			frappe.throw(_("PayU callback does not match the payment order."), frappe.AuthenticationError)
		settlement = _settle_processing_fee(
			attempt,
			posted.get("amount"),
			"PayU",
			posted.get("mihpayid"),
			posted,
		)
	else:
		frappe.throw(_("Unsupported payment gateway."), frappe.AuthenticationError)

	return settlement


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
	_require_warranty_dashboard_access(company)
	frappe.has_permission("Active VAS Plans", "read", throw=True)
	if frappe.db.exists("DocType", "CH VAS Ledger"):
		frappe.has_permission("CH VAS Ledger", "read", throw=True)
	try:
		limit = int(limit or 15)
	except (TypeError, ValueError):
		limit = 15
	limit = max(1, min(limit, 100))

	company = (company or "").strip() or None
	company_filter = get_company_filter_value(requested_company=company)

	# ── Active VAS Plans ────────────────────────────────────────────────────────
	plan_filters = {"docstatus": 1}
	if company_filter is not None:
		plan_filters["company"] = company_filter

	sold_plans = frappe.get_all(
		"Active VAS Plans",
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
		# CH Warranty Claim is linked to a active VAS plan via the `sold_plan` field
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
	if company_filter is not None:
		# CH Warranty Claim may or may not have a company field depending on schema;
		# only filter when the column exists to avoid 1054 errors.
		if frappe.db.has_column("CH Warranty Claim", "company"):
				claim_filters["company"] = company_filter

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
			"sold_plan": ("in", plan_names or ["__no_scoped_plan__"]),
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
		# Hydrate posting_date / customer for display from the linked active VAS plan.
		plan_lookup = {}
		need_lookup = {v["sold_plan"] for v in voucher_redemptions if v.get("sold_plan")}
		need_lookup -= set(plan_names)  # already in sold_plans payload
		if need_lookup:
			extra = frappe.get_all(
				"Active VAS Plans",
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


# ── Bot / Online Claim Creation ─────────────────────────────────────────────

@frappe.whitelist(allow_guest=False, methods=["POST"])
@rate_limit(limit=10, seconds=60)
def create_claim_from_bot(
    serial_no: str,
    customer_phone: str,
    issue_description: str,
    issue_category: str = "",
    photos: list | str | None = None,
    customer_name: str = "",
    customer_email: str = "",
    company: str = "",
) -> dict:
    """Create a Draft CH Warranty Claim from a bot or online channel.

    The claim is left as Draft (docstatus=0) pending VAS Manager review.
    The VAS Manager reviews photos and approves in a single step.

    Args:
        serial_no:          Device IMEI/serial number (required).
        customer_phone:     Customer mobile number (required, used to find/create customer).
        issue_description:  Free-text complaint (required).
        issue_category:     Optional issue category (e.g. "Screen Damage").
        photos:             List of Frappe file URLs already uploaded via /api/method/upload_file.
        customer_name:      Customer name (used if customer record not found by phone).
        customer_email:     Customer email.
        company:            Company; defaults to Global Default company.

    Returns:
        {"claim_name": str, "claim_status": str, "message": str}
    """
    import json as _json

    require_role_setting(
        "warranty_claim_intake_roles",
        ("CH Warranty Manager", "Service Manager", "Sales Manager", "Sales User"),
        action=_("create online warranty claims"),
    )
    frappe.has_permission("CH Warranty Claim", "create", throw=True)

    if not serial_no:
        frappe.throw(_("Serial number is required."), title=_("Bot Claim Error"))
    if not customer_phone:
        frappe.throw(_("Customer phone is required."), title=_("Bot Claim Error"))
    if not issue_description:
        frappe.throw(_("Issue description is required."), title=_("Bot Claim Error"))

    # Normalise photos arg (can come as JSON string from HTTP)
    if isinstance(photos, str):
        try:
            photos = _json.loads(photos)
        except Exception:
            photos = [photos] if photos else []
    photos = photos or []
    if not isinstance(photos, (list, tuple)):
        frappe.throw(_("Photos must be provided as a list."), frappe.ValidationError)

    # Resolve company
    if not company:
        company_scope = get_company_scope()
        if company_scope is None:
            company = frappe.db.get_single_value("Global Defaults", "default_company") or ""
        elif len(company_scope) == 1:
            company = company_scope[0]
        else:
            frappe.throw(_("Select one of your assigned companies."), frappe.PermissionError)
    ensure_company_access(company)

    from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
        resolve_lifecycle_name,
    )

    lifecycle_name = resolve_lifecycle_name(serial_no.strip())
    if not lifecycle_name:
        frappe.throw(_("Serial / IMEI was not found."), frappe.DoesNotExistError)
    lifecycle_company = frappe.db.get_value(
        "CH Serial Lifecycle", lifecycle_name, "current_company"
    )
    if lifecycle_company and lifecycle_company != company:
        frappe.throw(_("The serial belongs to another company."), frappe.PermissionError)

    # Find or create customer by phone
    customer = _resolve_customer_by_phone(customer_phone, customer_name, customer_email, company)

    # Build claim doc
    claim = frappe.new_doc("CH Warranty Claim")
    claim.serial_no    = serial_no.strip()
    claim.customer     = customer
    claim.customer_phone = customer_phone
    claim.customer_email = customer_email or ""
    claim.issue_description = issue_description
    claim.issue_category    = issue_category or ""
    claim.claim_channel     = "Online/Bot"
    claim.mode_of_service   = "Pickup"   # online always needs pickup
    claim.pickup_required   = 1
    claim.claim_date        = nowdate()
    claim.company           = company
    claim.reported_by       = frappe.session.user

    # Attach up to 6 photos to the 6 device-image fields
    image_fields = [
        "device_image_front", "device_image_back", "device_image_left",
        "device_image_right", "device_image_top", "device_image_bottom",
    ]
    for idx, photo_url in enumerate(photos[:6]):
        setattr(claim, image_fields[idx], photo_url)

    claim.insert()

    # Notify all VAS Managers that a new bot claim needs review
    _notify_vas_managers_new_bot_claim(claim)

    response_sla_hours = cint(
        frappe.db.get_single_value("CH VAS Settings", "claim_response_sla_hours") or 24
    )
    return {
        "claim_name": claim.name,
        "claim_status": "Draft",
        "message": _(
            "Your complaint has been registered (Ref: {0}). "
            "Our team will review and contact you within {1} hours."
        ).format(claim.name, response_sla_hours),
    }


def _resolve_customer_by_phone(phone: str, name: str, email: str, company: str) -> str:
    """Find existing customer by mobile_no or create one."""
    existing = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")
    if existing:
        return existing

    # Try contact lookup
    contact = frappe.db.get_value("Contact Phone", {"phone": phone}, "parent")
    if contact:
        linked = frappe.db.get_value(
            "Dynamic Link",
            {"parenttype": "Contact", "parent": contact, "link_doctype": "Customer"},
            "link_name",
        )
        if linked:
            return linked

    # Create new customer
    frappe.has_permission("Customer", "create", throw=True)
    cust = frappe.new_doc("Customer")
    cust.customer_name   = name or f"Online Customer {phone}"
    cust.customer_type   = "Individual"
    cust.customer_group  = frappe.db.get_single_value("Selling Settings", "customer_group") or "All Customer Groups"
    cust.territory       = frappe.db.get_single_value("Selling Settings", "territory") or "All Territories"
    cust.mobile_no       = phone
    cust.email_id        = email or ""
    cust.insert()
    return cust.name


def _notify_vas_managers_new_bot_claim(claim) -> None:
    """Notify bounded enabled business users scoped to the claim company."""
    configured_roles = frappe.db.get_single_value(
        "CH VAS Settings", "claim_notification_roles"
    )
    roles = configured_roles or get_role_setting("warranty_claim_management_roles", ())
    managers = get_enabled_role_users(roles, company=claim.company)
    for user in managers:
        try:
            frappe.get_doc({
                "doctype": "Notification Log",
                "subject": f"[Bot Claim] New warranty claim requires VAS review — {claim.name}",
                "email_content": (
                    f"Customer: {claim.customer_name or claim.customer}<br>"
                    f"Device Serial: {claim.serial_no}<br>"
                    f"Issue: {claim.issue_description}<br>"
                    f"<a href='/app/ch-warranty-claim/{claim.name}'>Open Claim</a>"
                ),
                "type": "Alert",
                "document_type": "CH Warranty Claim",
                "document_name": claim.name,
                "from_user": claim.reported_by,
                "for_user": user,
            }).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                title=f"Warranty bot claim notification failed for {user}",
                message=frappe.get_traceback(),
            )
