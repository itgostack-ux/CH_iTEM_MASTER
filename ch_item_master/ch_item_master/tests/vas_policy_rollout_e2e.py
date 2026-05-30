import frappe
from frappe.utils import nowdate, flt

from ch_item_master.ch_item_master.warranty_api import (
    issue_warranty_plan,
    validate_claim,
    initiate_warranty_claim,
)


def _ensure_issue_category(name: str) -> str:
    existing = frappe.db.get_value("Issue Category", {"category_name": name}, "name")
    if existing:
        return existing

    doc = frappe.new_doc("Issue Category")
    doc.category_name = name
    doc.is_active = 1
    doc.insert(ignore_permissions=True)
    return doc.name


def _ensure_service_item(item_code: str, item_name: str) -> str:
    if frappe.db.exists("Item", item_code):
        is_stock = frappe.db.get_value("Item", item_code, "is_stock_item")
        if is_stock:
            frappe.throw(f"{item_code} exists but is a stock item. Please convert to non-stock service item.")
        return item_code

    template = frappe.db.get_value(
        "Item",
        {"is_stock_item": 0, "disabled": 0, "gst_hsn_code": ["is", "set"]},
        ["item_group", "stock_uom", "gst_hsn_code"],
        as_dict=True,
    ) or {}

    doc = frappe.new_doc("Item")
    doc.item_code = item_code
    doc.item_name = item_name
    doc.item_group = template.get("item_group") or frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
    doc.stock_uom = template.get("stock_uom") or "Nos"
    doc.is_stock_item = 0
    doc.is_sales_item = 1
    doc.is_purchase_item = 0
    doc.include_item_in_manufacturing = 0
    if template.get("gst_hsn_code"):
        doc.gst_hsn_code = template.get("gst_hsn_code")
    doc.insert(ignore_permissions=True)
    return doc.name


def _upsert_plan(company: str, spec: dict) -> str:
    plan_name = spec["plan_name"]
    name = frappe.db.get_value("CH Warranty Plan", {"plan_name": plan_name}, "name")

    if name:
        doc = frappe.get_doc("CH Warranty Plan", name)
    else:
        doc = frappe.new_doc("CH Warranty Plan")

    doc.company = company
    doc.plan_name = plan_name
    doc.plan_type = spec["plan_type"]
    doc.coverage_scope = spec.get("coverage_scope", "Full Device")
    doc.purchase_window_hours = spec.get("purchase_window_hours", 0)
    doc.service_item = spec["service_item"]
    doc.status = "Active"
    doc.duration_months = spec["duration_months"]
    doc.coverage_description = spec.get("coverage_description")
    doc.max_claims = spec.get("max_claims", 0)
    doc.claims_per_year = spec.get("claims_per_year", 0)
    doc.deductible_amount = spec.get("deductible_amount", 0)
    doc.price = spec.get("price", 1)
    doc.pricing_mode = spec.get("pricing_mode", "Percentage of Device Price")
    doc.percentage_value = spec.get("percentage_value", 0)
    doc.attach_level = spec.get("attach_level", "Optional")
    doc.terms_and_conditions = spec.get("terms")
    doc.internal_notes = spec.get("notes")
    doc.requires_approval = spec.get("requires_approval", "If Company Pays")
    doc.company_share_percent = spec.get("company_share_percent", 100)

    doc.coverage_rules = []
    for rule in spec.get("coverage_rules", []):
        doc.append("coverage_rules", rule)

    doc.save(ignore_permissions=True)
    return doc.name


def _get_test_customer() -> str:
    customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
    if customer:
        return customer

    doc = frappe.new_doc("Customer")
    doc.customer_name = "VAS E2E Test Customer"
    doc.customer_type = "Individual"
    doc.customer_group = frappe.db.get_value("Customer Group", {}, "name") or "All Customer Groups"
    doc.territory = frappe.db.get_value("Territory", {}, "name") or "All Territories"
    doc.insert(ignore_permissions=True)
    return doc.name


def _get_test_device_item() -> str:
    item = frappe.db.get_value(
        "Item",
        {
            "disabled": 0,
            "has_serial_no": 1,
            "is_sales_item": 1,
        },
        "name",
    )
    if item:
        return item

    item = frappe.db.get_value("Item", {"disabled": 0, "is_sales_item": 1}, "name")
    if item:
        return item

    frappe.throw("No eligible sales item found for VAS E2E flow")


def _ensure_serial(item_code: str, company: str, serial_no: str) -> str:
    if frappe.db.exists("Serial No", serial_no):
        return serial_no

    doc = frappe.new_doc("Serial No")
    doc.serial_no = serial_no
    doc.item_code = item_code
    doc.company = company
    doc.insert(ignore_permissions=True)
    return doc.name


def run_vas_policy_rollout_e2e(company: str | None = None) -> dict:
    """Create EW/ADLD/OTSR/ADLD2 plans from policy sheet and run sell+claim E2E.

    This intentionally keeps all generated plans, sold plans, and claims for review.
    """
    if not company:
        company = frappe.db.get_single_value("Global Defaults", "default_company")
    if not company:
        company = frappe.db.get_value("Company", {}, "name")
    if not company:
        frappe.throw("No company available to run VAS rollout E2E")

    issue_screen = _ensure_issue_category("Screen Issues")
    issue_physical = _ensure_issue_category("Physical Damage")
    issue_water = _ensure_issue_category("Water Damage")
    issue_battery = _ensure_issue_category("Battery Issues")

    default_service_item = frappe.db.get_value(
        "CH Warranty Plan",
        {"status": "Active", "service_item": ["is", "set"]},
        "service_item",
    )
    if not default_service_item:
        default_service_item = frappe.db.get_value(
            "Item",
            {"is_stock_item": 0, "disabled": 0},
            "name",
        )
    if not default_service_item:
        frappe.throw("No non-stock service item found to map warranty plans")

    specs = [
        {
            "plan_name": "GoCare EW",
            "plan_type": "Extended Warranty",
            "service_item": default_service_item,
            "duration_months": 12,
            "pricing_mode": "Percentage of Device Price",
            "percentage_value": 3,
            "price": 1,
            "max_claims": 1,
            "claims_per_year": 1,
            "deductible_amount": 0,
            "coverage_scope": "Full Device",
            "purchase_window_hours": 0,
            "coverage_description": "Extends manufacturer warranty by 1 year for functional failures.",
            "terms": "Purchase within manufacturer warranty period; excludes physical/liquid damage, theft, accessories.",
            "notes": "Policy source: GoCare sheet EW.",
            "coverage_rules": [
                {"issue_type": issue_screen, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 1},
                {"issue_type": issue_battery, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 1},
                {"issue_type": issue_physical, "covered": 0, "coverage_percent": 0, "max_claim_per_issue": 0},
                {"issue_type": issue_water, "covered": 0, "coverage_percent": 0, "max_claim_per_issue": 0},
            ],
        },
        {
            "plan_name": "GoCare ADLD",
            "plan_type": "Protection Plan",
            "service_item": default_service_item,
            "duration_months": 12,
            "pricing_mode": "Percentage of Device Price",
            "percentage_value": 10,
            "price": 1,
            "max_claims": 2,
            "claims_per_year": 2,
            "deductible_amount": 999,
            "coverage_scope": "Full Device",
            "purchase_window_hours": 48,
            "coverage_description": "All physical and liquid damage coverage for one year.",
            "terms": "Apple co-pay Rs.1999; depreciation may apply for BER/total loss.",
            "notes": "Policy source: GoCare sheet ADLD.",
            "coverage_rules": [
                {"issue_type": issue_screen, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 2},
                {"issue_type": issue_physical, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 2},
                {"issue_type": issue_water, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 2},
            ],
        },
        {
            "plan_name": "GoCare OTSR",
            "plan_type": "Protection Plan",
            "service_item": default_service_item,
            "duration_months": 12,
            "pricing_mode": "Percentage of Device Price",
            "percentage_value": 7,
            "price": 1,
            "max_claims": 1,
            "claims_per_year": 1,
            "deductible_amount": 0,
            "coverage_scope": "Screen Only",
            "purchase_window_hours": 48,
            "coverage_description": "One-time screen replacement in plan year.",
            "terms": "GST/display cost may be charged as co-pay; excludes wear/scratches and non-screen parts.",
            "notes": "Policy source: GoCare sheet OTSR.",
            "coverage_rules": [
                {"issue_type": issue_screen, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 1},
                {"issue_type": issue_physical, "covered": 0, "coverage_percent": 0, "max_claim_per_issue": 0},
                {"issue_type": issue_water, "covered": 0, "coverage_percent": 0, "max_claim_per_issue": 0},
            ],
        },
        {
            "plan_name": "GoCare ADLD 2",
            "plan_type": "Protection Plan",
            "service_item": default_service_item,
            "duration_months": 24,
            "pricing_mode": "Percentage of Device Price",
            "percentage_value": 15,
            "price": 1,
            "max_claims": 2,
            "claims_per_year": 2,
            "deductible_amount": 999,
            "coverage_scope": "Full Device",
            "purchase_window_hours": 48,
            "coverage_description": "All physical and liquid damage coverage for two years.",
            "terms": "Android co-pay Rs.999; Apple co-pay Rs.1999; depreciation slab applies for BER/total loss.",
            "notes": "Policy source: GoCare sheet ADLD 2.",
            "coverage_rules": [
                {"issue_type": issue_screen, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 2},
                {"issue_type": issue_physical, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 2},
                {"issue_type": issue_water, "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 2},
            ],
        },
    ]

    created_plans = []
    for spec in specs:
        plan_name = _upsert_plan(company, spec)
        created_plans.append({"plan_name": spec["plan_name"], "docname": plan_name})

    customer = _get_test_customer()
    item_code = _get_test_device_item()

    e2e_results = []
    for spec in specs:
        plan_doc = frappe.db.get_value("CH Warranty Plan", {"plan_name": spec["plan_name"]}, "name")
        serial_no = f"VAS-{spec['plan_name'].replace(' ', '-').upper()}-{frappe.generate_hash(length=6).upper()}"
        _ensure_serial(item_code, company, serial_no)

        issue_type = issue_screen if spec["plan_name"] == "GoCare OTSR" else issue_physical
        estimated_repair = 5000

        sold = issue_warranty_plan(
            warranty_plan=plan_doc,
            customer=customer,
            item_code=item_code,
            serial_no=serial_no,
            start_date=nowdate(),
            company=company,
            plan_price=round(flt(estimated_repair) * flt(spec.get("percentage_value", 0)) / 100, 2),
        )
        sold_plan = sold.get("sold_plan")

        vc = validate_claim(sold_plan, issue_type=issue_type, estimate_amount=estimated_repair)

        claim = initiate_warranty_claim(
            serial_no=serial_no,
            customer=customer,
            item_code=item_code,
            company=company,
            issue_description=f"E2E validation for {spec['plan_name']}",
            issue_category=issue_type,
            reported_at_company=company,
            reported_at_store="VAS E2E Desk",
            estimated_repair_cost=estimated_repair,
            sold_plan=sold_plan,
        )

        ledger_event = frappe.db.get_value(
            "CH VAS Ledger",
            {"sold_plan": sold_plan, "event_type": "Plan Activated"},
            "name",
        )

        e2e_results.append(
            {
                "plan": spec["plan_name"],
                "warranty_plan": plan_doc,
                "sold_plan": sold_plan,
                "serial_no": serial_no,
                "claim_name": claim.get("claim_name"),
                "claim_status": claim.get("claim_status"),
                "coverage_type": claim.get("coverage_type"),
                "validate_claim": vc,
                "ledger_plan_activated": ledger_event,
            }
        )

    frappe.db.commit()

    return {
        "company": company,
        "created_or_updated_plans": created_plans,
        "customer": customer,
        "item_code": item_code,
        "e2e_results": e2e_results,
        "note": "All generated records were intentionally kept for review.",
    }
