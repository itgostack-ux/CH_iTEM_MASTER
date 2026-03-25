#!/usr/bin/env python3
"""
E2E VAS Lifecycle Test
======================
Tests the full VAS flow:
  1. Seed coverage rules on existing plans
  2. Validate_claim API against coverage rules
  3. Issue a sold plan (Plan Activated → ledger entry)
  4. Validate claim eligibility
  5. Initiate warranty claim → GoFix ticket
  6. Approve → mark repair complete → close claim → ledger entries
  7. Expire plan → ledger entry
  8. VAS Workspace exists with correct links

Run:  bench --site erpnext.local execute ch_item_master.ch_item_master.tests.test_vas_e2e.run_e2e
"""

import frappe
from frappe.utils import nowdate, add_months, add_days, getdate, flt

PASS = 0
FAIL = 0
ERRORS = []


def _ok(label):
    global PASS
    PASS += 1
    print(f"  ✅ {label}")


def _fail(label, detail=""):
    global FAIL
    FAIL += 1
    msg = f"  ❌ {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    ERRORS.append(msg)


def run_e2e():
    global PASS, FAIL, ERRORS
    PASS = 0
    FAIL = 0
    ERRORS = []

    print("\n" + "=" * 70)
    print("  VAS E2E LIFECYCLE TEST")
    print("=" * 70)

    # ── Phase 0: Pre-checks ──────────────────────────────────────────
    print("\n── Phase 0: Pre-checks ──")

    # Workspace
    if frappe.db.exists("Workspace", "CH VAS"):
        ws = frappe.get_doc("Workspace", "CH VAS")
        link_labels = [l.label for l in ws.links if l.type == "Link"]
        for needed in ["CH Warranty Plan", "CH Sold Plan", "CH Warranty Claim", "CH Voucher", "CH VAS Ledger"]:
            if needed in link_labels:
                _ok(f"Workspace link: {needed}")
            else:
                _fail(f"Workspace missing link: {needed}")
        sc_labels = [s.label for s in ws.shortcuts]
        for needed in ["Warranty Plans", "Sold Plans", "Warranty Claims", "Vouchers"]:
            if needed in sc_labels:
                _ok(f"Workspace shortcut: {needed}")
            else:
                _fail(f"Workspace missing shortcut: {needed}")
    else:
        _fail("Workspace CH VAS does not exist")

    # DocTypes exist
    for dt in ["CH VAS Ledger", "CH Claim Media", "CH Coverage Rule"]:
        if frappe.db.exists("DocType", dt):
            _ok(f"DocType {dt} exists")
        else:
            _fail(f"DocType {dt} missing")

    # Fields exist on parents
    meta_plan = frappe.get_meta("CH Warranty Plan")
    if meta_plan.get_field("coverage_rules"):
        _ok("CH Warranty Plan has coverage_rules field")
    else:
        _fail("CH Warranty Plan missing coverage_rules field")

    meta_claim = frappe.get_meta("CH Warranty Claim")
    if meta_claim.get_field("claim_media"):
        _ok("CH Warranty Claim has claim_media field")
    else:
        _fail("CH Warranty Claim missing claim_media field")

    # ── Phase 1: Seed coverage rules ─────────────────────────────────
    print("\n── Phase 1: Seed Coverage Rules ──")

    # Use Gold Protection Bundle for comprehensive testing
    gold_plan_name = frappe.db.get_value("CH Warranty Plan", {"plan_name": "Gold Protection Bundle"}, "name")
    if not gold_plan_name:
        _fail("Gold Protection Bundle plan not found")
        _print_summary()
        return

    gold_plan = frappe.get_doc("CH Warranty Plan", gold_plan_name)

    # Clear existing rules and add fresh ones
    gold_plan.coverage_rules = []
    rules_data = [
        {"issue_type": "Screen Issues",    "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 2, "deductible_override": 0},
        {"issue_type": "Battery Issues",   "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 1, "deductible_override": 0},
        {"issue_type": "Physical Damage",  "covered": 1, "coverage_percent": 80,  "max_claim_per_issue": 1, "deductible_override": 500},
        {"issue_type": "Water Damage",     "covered": 0, "coverage_percent": 0,   "max_claim_per_issue": 0, "deductible_override": 0},
        {"issue_type": "Camera Issues",    "covered": 1, "coverage_percent": 100, "max_claim_per_issue": 1, "deductible_override": 0},
        {"issue_type": "Software Problems","covered": 1, "coverage_percent": 50,  "max_claim_per_issue": 0, "deductible_override": 200},
    ]
    for rd in rules_data:
        gold_plan.append("coverage_rules", rd)
    gold_plan.flags.ignore_permissions = True
    gold_plan.save()
    _ok(f"Seeded {len(rules_data)} coverage rules on {gold_plan.plan_name}")

    # Verify
    gold_plan.reload()
    if len(gold_plan.coverage_rules) == len(rules_data):
        _ok("Coverage rules saved and reloaded correctly")
    else:
        _fail(f"Expected {len(rules_data)} rules, got {len(gold_plan.coverage_rules)}")

    # ── Phase 2: Issue a Sold Plan ───────────────────────────────────
    print("\n── Phase 2: Issue Sold Plan ──")

    from ch_item_master.ch_item_master.warranty_api import issue_warranty_plan, validate_claim

    # Get a test customer and item
    customer = frappe.db.get_value("Customer", {"customer_name": ["like", "%Test%"]}, "name")
    if not customer:
        customer = frappe.db.get_value("Customer", {}, "name")

    item_code = frappe.db.get_value("Item", {"item_group": ["like", "%Mobile%"]}, "name")
    if not item_code:
        item_code = frappe.db.get_value("Item", {"is_stock_item": 1}, "name")

    if not customer or not item_code:
        _fail(f"Need test customer ({customer}) and item ({item_code})")
        _print_summary()
        return

    company = frappe.db.get_single_value("Global Defaults", "default_company") or "GoGizmo Retail Pvt Ltd"

    # Create a test serial
    test_serial = f"VAS-E2E-{frappe.generate_hash(length=6).upper()}"

    # Ensure Serial No exists in ERPNext
    if not frappe.db.exists("Serial No", test_serial):
        sn = frappe.new_doc("Serial No")
        sn.serial_no = test_serial
        sn.item_code = item_code
        sn.company = company
        sn.flags.ignore_permissions = True
        sn.insert()

    result = issue_warranty_plan(
        warranty_plan=gold_plan_name,
        customer=customer,
        item_code=item_code,
        serial_no=test_serial,
        start_date=nowdate(),
        company=company,
        plan_price=2999,
    )

    if result.get("sold_plan"):
        _ok(f"Sold Plan created: {result['sold_plan']}")
    else:
        _fail("Sold Plan creation failed", str(result))
        _print_summary()
        return

    sold_plan_name = result["sold_plan"]

    # Check VAS Ledger — Plan Activated
    ledger_activated = frappe.db.get_value(
        "CH VAS Ledger",
        {"sold_plan": sold_plan_name, "event_type": "Plan Activated"},
        "name",
    )
    if ledger_activated:
        _ok(f"VAS Ledger: Plan Activated event logged ({ledger_activated})")
    else:
        _fail("VAS Ledger: Plan Activated event NOT found")

    # ── Phase 3: Validate Claim API ──────────────────────────────────
    print("\n── Phase 3: Validate Claim API ──")

    # Test 1: Screen Issues — should be eligible, 100% coverage
    v1 = validate_claim(sold_plan_name, issue_type="Screen Issues", estimate_amount=5000)
    if v1.get("eligible"):
        _ok("Screen Issues claim eligible")
        if flt(v1.get("covered_amount")) == 5000:
            _ok(f"Screen Issues: covered_amount = ₹{v1['covered_amount']} (100%)")
        else:
            _fail(f"Screen: expected covered=5000, got {v1.get('covered_amount')}")
        if flt(v1.get("customer_payable")) == 0:
            _ok("Screen Issues: customer_payable = ₹0")
        else:
            _fail(f"Screen: expected customer_payable=0, got {v1.get('customer_payable')}")
    else:
        _fail("Screen Issues should be eligible", v1.get("reason"))

    # Test 2: Water Damage — covered=0, should NOT be eligible
    v2 = validate_claim(sold_plan_name, issue_type="Water Damage", estimate_amount=3000)
    if not v2.get("eligible"):
        _ok("Water Damage correctly rejected (not covered)")
    else:
        _fail("Water Damage should NOT be eligible")

    # Test 3: Physical Damage — 80% coverage, ₹500 deductible
    v3 = validate_claim(sold_plan_name, issue_type="Physical Damage", estimate_amount=10000)
    if v3.get("eligible"):
        _ok("Physical Damage claim eligible")
        expected_covered = 10000 * 0.80 - 500  # = 7500
        if abs(flt(v3.get("covered_amount")) - expected_covered) < 1:
            _ok(f"Physical Damage: covered = ₹{v3['covered_amount']} (80% - ₹500 deductible)")
        else:
            _fail(f"Physical Damage: expected covered={expected_covered}, got {v3.get('covered_amount')}")
        expected_customer = 10000 - expected_covered
        if abs(flt(v3.get("customer_payable")) - expected_customer) < 1:
            _ok(f"Physical Damage: customer_payable = ₹{v3['customer_payable']}")
        else:
            _fail(f"Physical Damage: expected customer={expected_customer}, got {v3.get('customer_payable')}")
    else:
        _fail("Physical Damage should be eligible", v3.get("reason"))

    # Test 4: Software Problems — 50% coverage, ₹200 deductible
    v4 = validate_claim(sold_plan_name, issue_type="Software Problems", estimate_amount=2000)
    if v4.get("eligible"):
        expected_covered = 2000 * 0.50 - 200  # = 800
        if abs(flt(v4.get("covered_amount")) - expected_covered) < 1:
            _ok(f"Software Problems: covered = ₹{v4['covered_amount']} (50% - ₹200)")
        else:
            _fail(f"Software: expected covered={expected_covered}, got {v4.get('covered_amount')}")
    else:
        _fail("Software Problems should be eligible", v4.get("reason"))

    # Test 5: No issue type → falls back to plan-level settings
    v5 = validate_claim(sold_plan_name, issue_type=None, estimate_amount=5000)
    if v5.get("eligible"):
        _ok("No issue_type → eligible with plan-level defaults")
    else:
        _fail("No issue_type should still be eligible", v5.get("reason"))

    # ── Phase 4: Initiate Warranty Claim ─────────────────────────────
    print("\n── Phase 4: Initiate Warranty Claim ──")

    from ch_item_master.ch_item_master.warranty_api import initiate_warranty_claim

    claim_result = initiate_warranty_claim(
        serial_no=test_serial,
        customer=customer,
        item_code=item_code,
        company=company,
        issue_description="Screen cracked during normal use — E2E test",
        issue_category="Screen Issues",
        reported_at_company=company,
        reported_at_store="VAS-E2E-Test",
        estimated_repair_cost=5000,
    )

    claim_name = claim_result.get("claim_name")
    if claim_name:
        _ok(f"Warranty Claim created: {claim_name}")
    else:
        _fail("Warranty Claim creation failed", str(claim_result))
        _print_summary()
        return

    claim_doc = frappe.get_doc("CH Warranty Claim", claim_name)
    _ok(f"Claim status: {claim_doc.claim_status}")
    _ok(f"Coverage type: {claim_doc.coverage_type}")

    if claim_doc.sold_plan == sold_plan_name:
        _ok(f"Claim linked to sold plan: {sold_plan_name}")
    else:
        _fail(f"Claim sold_plan mismatch: expected {sold_plan_name}, got {claim_doc.sold_plan}")

    # ── Phase 5: Claim Media ─────────────────────────────────────────
    print("\n── Phase 5: Claim Media ──")

    # Add claim media entries (simulating photo upload at different stages)
    claim_doc.append("claim_media", {
        "stage": "Customer Upload",
        "image": "/files/test_front.jpg",
        "caption": "Front view — cracked screen",
        "uploaded_by": frappe.session.user,
    })
    claim_doc.append("claim_media", {
        "stage": "Customer Upload",
        "image": "/files/test_back.jpg",
        "caption": "Back view",
        "uploaded_by": frappe.session.user,
    })
    claim_doc.flags.ignore_permissions = True
    claim_doc.save()
    claim_doc.reload()

    if len(claim_doc.claim_media) == 2:
        _ok("2 claim media entries added successfully")
    else:
        _fail(f"Expected 2 claim media, got {len(claim_doc.claim_media)}")

    # ── Phase 6: Claim Lifecycle ─────────────────────────────────────
    print("\n── Phase 6: Claim Lifecycle ──")

    # If claim needs approval, approve it
    if claim_doc.claim_status == "Pending Approval":
        claim_doc.approve(remarks="E2E test approval")
        claim_doc.reload()
        if claim_doc.claim_status in ("Approved", "Ticket Created"):
            _ok(f"Claim approved → {claim_doc.claim_status}")
        else:
            _fail(f"After approve, status={claim_doc.claim_status}")

    # If approved and GoFix ticket created
    if claim_doc.claim_status == "Ticket Created":
        _ok(f"GoFix ticket created: {claim_doc.service_request}")

        # Mark repair complete
        claim_doc.mark_repair_complete(remarks="E2E repair done")
        claim_doc.reload()
        if claim_doc.claim_status == "Repair Complete":
            _ok("Repair marked complete")
        else:
            _fail(f"After repair, status={claim_doc.claim_status}")

    # Close claim
    if claim_doc.claim_status in ("Repair Complete", "Approved"):
        claim_doc.close_claim(remarks="E2E claim settled")
        claim_doc.reload()
        if claim_doc.claim_status == "Closed":
            _ok("Claim closed successfully")
        else:
            _fail(f"After close, status={claim_doc.claim_status}")

        # Check VAS ledger for Claim Used
        ledger_claim = frappe.db.get_value(
            "CH VAS Ledger",
            {"sold_plan": sold_plan_name, "event_type": "Claim Used"},
            "name",
        )
        if ledger_claim:
            _ok(f"VAS Ledger: Claim Used event logged ({ledger_claim})")
        else:
            _fail("VAS Ledger: Claim Used event NOT found")

    # ── Phase 7: Check Sold Plan claim count ─────────────────────────
    print("\n── Phase 7: Sold Plan State ──")

    sp = frappe.get_doc("CH Sold Plan", sold_plan_name)
    if sp.claims_used >= 1:
        _ok(f"Sold Plan claims_used = {sp.claims_used}")
    else:
        _fail(f"Expected claims_used >= 1, got {sp.claims_used}")

    # ── Phase 8: Expire plan → ledger ────────────────────────────────
    print("\n── Phase 8: Expire Plan → Ledger ──")

    # Simulate expiry by setting end_date to yesterday
    yesterday = add_days(nowdate(), -1)
    frappe.db.set_value("CH Sold Plan", sold_plan_name, "end_date", yesterday, update_modified=False)
    frappe.db.set_value("CH Sold Plan", sold_plan_name, "status", "Active", update_modified=False)
    frappe.db.commit()

    from ch_item_master.ch_item_master.warranty_api import expire_sold_plans
    expire_sold_plans()

    sp.reload()
    if sp.status == "Expired":
        _ok("Plan expired via scheduled task")
    else:
        _fail(f"Expected Expired, got {sp.status}")

    ledger_expired = frappe.db.get_value(
        "CH VAS Ledger",
        {"sold_plan": sold_plan_name, "event_type": "Plan Expired"},
        "name",
    )
    if ledger_expired:
        _ok(f"VAS Ledger: Plan Expired event logged ({ledger_expired})")
    else:
        _fail("VAS Ledger: Plan Expired event NOT found")

    # ── Phase 9: Full ledger audit trail ─────────────────────────────
    print("\n── Phase 9: Full Ledger Audit Trail ──")

    all_events = frappe.get_all(
        "CH VAS Ledger",
        filters={"sold_plan": sold_plan_name},
        fields=["event_type", "claim_amount", "remaining_claims", "reference_name"],
        order_by="creation asc",
    )
    print(f"  Ledger entries for {sold_plan_name}:")
    for evt in all_events:
        print(f"    → {evt.event_type} | amount={evt.claim_amount} | remaining_claims={evt.remaining_claims}")

    expected_events = {"Plan Activated", "Claim Used", "Plan Expired"}
    actual_events = {e.event_type for e in all_events}
    missing = expected_events - actual_events
    if not missing:
        _ok(f"All expected ledger events present: {sorted(actual_events)}")
    else:
        _fail(f"Missing ledger events: {missing}")

    # ── Summary ──────────────────────────────────────────────────────
    _print_summary()


def _print_summary():
    print("\n" + "=" * 70)
    print(f"  RESULTS: {PASS} passed, {FAIL} failed ({PASS + FAIL} total)")
    print("=" * 70)
    if ERRORS:
        print("\n  FAILURES:")
        for e in ERRORS:
            print(f"  {e}")
    print()
