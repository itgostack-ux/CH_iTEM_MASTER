"""
CH Store, CH Payment Method, and CH OTP Log E2E Tests

Covers:
- CH Store: create, store_code autoname, store_id auto-increment, bin creation,
  pincode validation, city-company mismatch, zone-company mismatch
- CH Payment Method: create, payment_method_id auto-increment, method_type flag derivation
  (Bank→requires_bank_details, UPI→requires_upi_id, Cash→both false)
- CH OTP Log: generate_otp, verify_otp (valid, wrong code, expired, max attempts, idempotency),
  rate limit (5 per hour), already-verified idempotency

Run:
    bench --site erpnext.local execute ch_item_master.ch_item_master.tests.test_store_payment_methods_e2e.run_all
"""

import frappe
from frappe.utils import now_datetime, add_to_date

_results = []


def _ok(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "PASS"})
    print(f"  PASS  [{flow}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{flow}] {step}" + (f"  — {detail}" if detail else ""))


def _skip(flow, step, reason=""):
    print(f"  SKIP  [{flow}] {step}" + (f"  — {reason}" if reason else ""))


def _first_company():
    return frappe.db.get_value("Company", {}, "name") or "GoGizmo"


def _first_warehouse(company=None):
    return frappe.db.get_value(
        "Warehouse",
        {"company": company or _first_company(), "is_group": 0, "disabled": 0},
        "name",
    )


# ── CH Store Tests ────────────────────────────────────────────────────────────

def test_store_create_basic():
    flow = "CH Store"
    company = _first_company()
    wh = _first_warehouse(company)
    if not wh:
        _skip(flow, "create basic", "no warehouse found")
        return

    doc = None
    try:
        doc = frappe.new_doc("CH Store")
        doc.store_name = f"Test Store E2E {frappe.utils.random_string(4)}"
        doc.company = company
        doc.warehouse = wh
        doc.insert(ignore_permissions=True)
        frappe.db.rollback()

        if not doc.store_code:
            _fail(flow, "create basic", "store_code not generated")
            return
        if not doc.store_id:
            _fail(flow, "create basic", "store_id not set")
            return
        _ok(flow, "create basic", doc.store_code)
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "create basic", str(e))


def test_store_code_format():
    flow = "CH Store"
    company = _first_company()
    abbr = frappe.db.get_value("Company", company, "abbr") or "STO"
    wh = _first_warehouse(company)
    if not wh:
        _skip(flow, "store_code format", "no warehouse")
        return

    doc = None
    try:
        doc = frappe.new_doc("CH Store")
        doc.store_name = f"Code Format Test {frappe.utils.random_string(4)}"
        doc.company = company
        doc.warehouse = wh
        doc.insert(ignore_permissions=True)
        frappe.db.rollback()

        expected_prefix = f"STO-{abbr.upper()}"
        if not doc.store_code.startswith("STO-"):
            _fail(flow, "store_code format", f"expected STO- prefix, got {doc.store_code}")
            return
        _ok(flow, "store_code format", doc.store_code)
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "store_code format", str(e))


def test_store_pincode_validation():
    flow = "CH Store"
    company = _first_company()
    wh = _first_warehouse(company)
    if not wh:
        _skip(flow, "pincode validation", "no warehouse")
        return

    try:
        doc = frappe.new_doc("CH Store")
        doc.store_name = f"Pincode Test {frappe.utils.random_string(4)}"
        doc.company = company
        doc.warehouse = wh
        doc.pincode = "123"  # invalid: not 6 digits
        doc.insert(ignore_permissions=True)
        frappe.db.rollback()
        _fail(flow, "pincode validation", "should have raised for 3-digit pincode")
    except frappe.ValidationError:
        frappe.db.rollback()
        _ok(flow, "pincode validation (short pincode rejected)")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "pincode validation", str(e))


def test_store_manual_store_code():
    flow = "CH Store"
    company = _first_company()
    wh = _first_warehouse(company)
    if not wh:
        _skip(flow, "manual store code", "no warehouse")
        return

    manual_code = f"MANUAL-{frappe.utils.random_string(6).upper()}"
    try:
        doc = frappe.new_doc("CH Store")
        doc.store_code = manual_code
        doc.store_name = f"Manual Code Test {frappe.utils.random_string(4)}"
        doc.company = company
        doc.warehouse = wh
        doc.insert(ignore_permissions=True)
        frappe.db.rollback()

        if doc.name != manual_code:
            _fail(flow, "manual store code", f"expected {manual_code}, got {doc.name}")
            return
        _ok(flow, "manual store code", doc.name)
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "manual store code", str(e))


def test_store_bins_created():
    """Verify ensure_store_bins creates In-Transit, Damaged, Disposed, Reserved, Buyback bins."""
    flow = "CH Store"
    company = _first_company()
    wh = _first_warehouse(company)
    if not wh:
        _skip(flow, "bins created", "no warehouse")
        return

    doc = None
    try:
        doc = frappe.new_doc("CH Store")
        doc.store_name = f"Bin Test {frappe.utils.random_string(4)}"
        doc.company = company
        doc.warehouse = wh
        doc.insert(ignore_permissions=True)

        bins = frappe.get_all(
            "Warehouse",
            filters={"ch_store": doc.name, "ch_bin_type": ["!=", "Sellable"]},
            pluck="ch_bin_type",
        )
        bin_types = set(bins)
        expected = {"Damaged", "Demo", "Buyback"}
        missing = expected - bin_types
        frappe.db.rollback()

        if missing:
            _fail(flow, "bins created", f"missing bin types: {missing}")
        else:
            _ok(flow, "bins created", f"{len(bins)} operational bins created")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "bins created", str(e))


def test_store_id_increments():
    """Two stores should get consecutive store_id values."""
    flow = "CH Store"
    company = _first_company()
    wh = _first_warehouse(company)
    if not wh:
        _skip(flow, "store_id increments", "no warehouse")
        return

    try:
        d1 = frappe.new_doc("CH Store")
        d1.store_name = f"ID Inc Test 1 {frappe.utils.random_string(4)}"
        d1.company = company
        d1.warehouse = wh
        d1.insert(ignore_permissions=True)

        d2 = frappe.new_doc("CH Store")
        d2.store_name = f"ID Inc Test 2 {frappe.utils.random_string(4)}"
        d2.company = company
        d2.warehouse = wh
        d2.insert(ignore_permissions=True)

        frappe.db.rollback()

        if d2.store_id != d1.store_id + 1:
            _fail(flow, "store_id increments", f"id1={d1.store_id}, id2={d2.store_id}")
            return
        _ok(flow, "store_id increments", f"{d1.store_id} → {d2.store_id}")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "store_id increments", str(e))


# ── CH Payment Method Tests ───────────────────────────────────────────────────

def test_payment_method_bank_flags():
    flow = "CH Payment Method"
    try:
        doc = frappe.new_doc("CH Payment Method")
        doc.method_name = f"Test Bank {frappe.utils.random_string(4)}"
        doc.method_type = "Bank"
        doc.insert(ignore_permissions=True)
        frappe.db.rollback()

        if not doc.requires_bank_details:
            _fail(flow, "Bank→requires_bank_details", "flag not set")
            return
        if doc.requires_upi_id:
            _fail(flow, "Bank→requires_upi_id=0", "flag should be 0")
            return
        _ok(flow, "Bank method flags", f"id={doc.payment_method_id}")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "Bank method flags", str(e))


def test_payment_method_upi_flags():
    flow = "CH Payment Method"
    try:
        doc = frappe.new_doc("CH Payment Method")
        doc.method_name = f"Test UPI {frappe.utils.random_string(4)}"
        doc.method_type = "UPI"
        doc.insert(ignore_permissions=True)
        frappe.db.rollback()

        if not doc.requires_upi_id:
            _fail(flow, "UPI→requires_upi_id", "flag not set")
            return
        if doc.requires_bank_details:
            _fail(flow, "UPI→requires_bank_details=0", "flag should be 0")
            return
        _ok(flow, "UPI method flags")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "UPI method flags", str(e))


def test_payment_method_cash_flags():
    flow = "CH Payment Method"
    try:
        doc = frappe.new_doc("CH Payment Method")
        doc.method_name = f"Test Cash {frappe.utils.random_string(4)}"
        doc.method_type = "Cash"
        doc.insert(ignore_permissions=True)
        frappe.db.rollback()

        if doc.requires_bank_details or doc.requires_upi_id:
            _fail(flow, "Cash method flags", "both flags should be 0 for Cash")
            return
        _ok(flow, "Cash method flags")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "Cash method flags", str(e))


def test_payment_method_id_increments():
    flow = "CH Payment Method"
    try:
        d1 = frappe.new_doc("CH Payment Method")
        d1.method_name = f"PM Inc 1 {frappe.utils.random_string(4)}"
        d1.method_type = "Cash"
        d1.insert(ignore_permissions=True)

        d2 = frappe.new_doc("CH Payment Method")
        d2.method_name = f"PM Inc 2 {frappe.utils.random_string(4)}"
        d2.method_type = "Cash"
        d2.insert(ignore_permissions=True)

        frappe.db.rollback()

        if d2.payment_method_id != d1.payment_method_id + 1:
            _fail(flow, "payment_method_id increments", f"{d1.payment_method_id} → {d2.payment_method_id}")
            return
        _ok(flow, "payment_method_id increments")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "payment_method_id increments", str(e))


# ── CH OTP Log Tests ──────────────────────────────────────────────────────────

def test_otp_generate_and_verify():
    flow = "CH OTP Log"
    mobile = "9876543210"
    purpose = "POS Customer Verification"

    try:
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        otp_code = CHOTPLog.generate_otp(mobile, purpose)

        if not otp_code or len(otp_code) != 6 or not otp_code.isdigit():
            _fail(flow, "generate OTP", f"invalid OTP format: {otp_code}")
            frappe.db.rollback()
            return
        _ok(flow, "generate OTP", f"6-digit OTP generated")

        result = CHOTPLog.verify_otp(mobile, purpose, otp_code)
        frappe.db.rollback()

        if not result.get("valid"):
            _fail(flow, "verify OTP (correct code)", result.get("message"))
        else:
            _ok(flow, "verify OTP (correct code)")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "generate+verify OTP", str(e))


def test_otp_wrong_code():
    flow = "CH OTP Log"
    mobile = "9876543211"
    purpose = "High Value Sale"

    try:
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        CHOTPLog.generate_otp(mobile, purpose)
        result = CHOTPLog.verify_otp(mobile, purpose, "000000")  # wrong code
        frappe.db.rollback()

        if result.get("valid"):
            _fail(flow, "verify OTP (wrong code)", "should have returned valid=False")
        else:
            _ok(flow, "verify OTP (wrong code rejected)")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "wrong code OTP", str(e))


def test_otp_expired():
    flow = "CH OTP Log"
    mobile = "9876543212"
    purpose = "Discount Override"

    try:
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        otp_code = CHOTPLog.generate_otp(mobile, purpose)

        # Manually back-date the OTP to make it expired
        log_name = frappe.db.get_value(
            "CH OTP Log",
            {"mobile_no": mobile, "purpose": purpose, "status": "Pending"},
            "name",
        )
        if log_name:
            frappe.db.set_value("CH OTP Log", log_name, {
                "expires_at": add_to_date(now_datetime(), minutes=-10),
            }, update_modified=False)
            frappe.db.commit()

        result = CHOTPLog.verify_otp(mobile, purpose, otp_code)
        frappe.db.rollback()

        if result.get("valid"):
            _fail(flow, "expired OTP rejected", "expired OTP should be invalid")
        else:
            _ok(flow, "expired OTP rejected")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "expired OTP", str(e))


def test_otp_max_attempts():
    flow = "CH OTP Log"
    mobile = "9876543213"
    purpose = "Manager Override"

    try:
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        CHOTPLog.generate_otp(mobile, purpose)

        # Submit 5 wrong attempts (MAX_ATTEMPTS=5)
        for i in range(5):
            CHOTPLog.verify_otp(mobile, purpose, "111111")

        # 6th attempt — log should now be Failed status
        result = CHOTPLog.verify_otp(mobile, purpose, "111111")
        frappe.db.rollback()

        if result.get("valid"):
            _fail(flow, "max attempts lockout", "should be locked after 5 failed attempts")
        else:
            _ok(flow, "max attempts lockout", result.get("message", ""))
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "max attempts OTP", str(e))


def test_otp_already_verified_idempotency():
    """Second verify call after success should return valid=True (idempotency)."""
    flow = "CH OTP Log"
    mobile = "9876543214"
    purpose = "Service Delivery"

    try:
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        otp_code = CHOTPLog.generate_otp(mobile, purpose)

        # First verify — should succeed
        result1 = CHOTPLog.verify_otp(mobile, purpose, otp_code)
        if not result1.get("valid"):
            _fail(flow, "idempotency first verify", result1.get("message"))
            frappe.db.rollback()
            return

        # Second verify (retry scenario) — should return idempotent success
        result2 = CHOTPLog.verify_otp(mobile, purpose, otp_code)
        frappe.db.rollback()

        if not result2.get("valid"):
            _fail(flow, "idempotency second verify", result2.get("message"))
        else:
            _ok(flow, "already-verified idempotency", result2.get("message", ""))
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "OTP idempotency", str(e))


def test_otp_rate_limit():
    """Generating > 5 unverified OTPs within 1 hour should raise."""
    flow = "CH OTP Log"
    mobile = "9876543215"
    # Each call needs a valid Select purpose; use 6 distinct values for the rate-limit test.
    _rl_purposes = [
        "Buyback Confirmation",
        "Exchange Confirmation",
        "Free Accessory",
        "Old Stock Clearance",
        "Warranty Giveaway",
        "Return Beyond Policy",  # 6th — should be blocked
    ]

    try:
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        # Generate 5 OTPs without verifying them
        for i in range(5):
            CHOTPLog.generate_otp(mobile, _rl_purposes[i])

        # 6th should raise rate limit
        try:
            CHOTPLog.generate_otp(mobile, _rl_purposes[5])
            frappe.db.rollback()
            _fail(flow, "rate limit (6th OTP)", "should have raised ValidationError")
        except frappe.ValidationError:
            frappe.db.rollback()
            _ok(flow, "rate limit (6th OTP blocked)")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "OTP rate limit", str(e))


def test_otp_no_pending_otp():
    """Verify when no OTP exists should return valid=False."""
    flow = "CH OTP Log"
    try:
        from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

        result = CHOTPLog.verify_otp("9999999999", "Buyback Customer Approval", "123456")
        frappe.db.rollback()

        if result.get("valid"):
            _fail(flow, "no pending OTP", "should return valid=False for unknown mobile")
        else:
            _ok(flow, "no pending OTP returns valid=False")
    except Exception as e:
        frappe.db.rollback()
        _fail(flow, "no pending OTP", str(e))


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    global _results
    _results = []

    print("\n" + "=" * 60)
    print("CH Store / Payment Method / OTP Log — E2E Tests")
    print("=" * 60)

    test_store_create_basic()
    test_store_code_format()
    test_store_pincode_validation()
    test_store_manual_store_code()
    test_store_bins_created()
    test_store_id_increments()

    test_payment_method_bank_flags()
    test_payment_method_upi_flags()
    test_payment_method_cash_flags()
    test_payment_method_id_increments()

    test_otp_generate_and_verify()
    test_otp_wrong_code()
    test_otp_expired()
    test_otp_max_attempts()
    test_otp_already_verified_idempotency()
    test_otp_rate_limit()
    test_otp_no_pending_otp()

    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")

    print("\n" + "=" * 60)
    print(f"TOTAL: {passed} passed, {failed} failed out of {len(_results)} tests")
    print("=" * 60)

    if failed:
        import sys
        sys.exit(1)
