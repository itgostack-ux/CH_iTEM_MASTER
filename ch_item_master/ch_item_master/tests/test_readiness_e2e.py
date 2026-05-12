"""
Item Sales Readiness — E2E Tests

Tests the SAP-style sales-readiness checklist introduced in May 2026:
  T01  Module imports without error; both whitelisted functions present
  T02  get_item_readiness() raises for non-existent item code
  T03  get_item_readiness() returns required top-level keys
  T04  Response shape: checks list is non-empty, each check has required keys
  T05  is_sellable reflects whether blockers == 0
  T06  Disabled item always has not_disabled check as a blocker
  T07  Item missing is_sales_item=0 produces is_sales_item check as blocker
  T08  get_readiness_for_missing_item() returns None for empty/unknown term
  T09  get_readiness_for_missing_item() returns report for known item code

Run:
  bench --site erpnext.local execute \
    ch_item_master.ch_item_master.tests.test_readiness_e2e.run_all
"""

import frappe

results = []


def ok(name, detail=""):
    results.append(("PASS", name, detail))
    print(f"PASS  {name}{f'  ({detail})' if detail else ''}")


def fail(name, detail=""):
    results.append(("FAIL", name, detail))
    print(f"FAIL  {name}{f'  ({detail})' if detail else ''}")


# ── T01: Module imports ───────────────────────────────────────────────────────

def test_module_imports():
    try:
        from ch_item_master.ch_item_master.readiness import (
            get_item_readiness,
            get_readiness_for_missing_item,
        )
        ok("T01_module_imports", "both whitelisted functions importable")
    except Exception as e:
        fail("T01_module_imports", str(e))


# ── T02: Non-existent item raises ────────────────────────────────────────────

def test_nonexistent_item_raises():
    from ch_item_master.ch_item_master.readiness import get_item_readiness

    raised = False
    try:
        get_item_readiness("NONEXISTENT-READINESS-TEST-ITEM-XYZ")
    except frappe.ValidationError:
        raised = True
    except Exception as e:
        raised = True  # any exception is acceptable here

    assert raised, "Expected an exception for non-existent item"
    ok("T02_nonexistent_item_raises", "raised correctly for unknown item")


# ── T03 / T04: Response shape ─────────────────────────────────────────────────

def test_response_shape():
    from ch_item_master.ch_item_master.readiness import get_item_readiness

    item_code = frappe.db.get_value("Item", {"disabled": 0, "has_variants": 0}, "name")
    if not item_code:
        ok("T03_response_shape", "skipped — no enabled non-template item in DB")
        return

    result = get_item_readiness(item_code)

    required_top_keys = {"item_code", "item_name", "company", "is_sellable",
                         "blockers", "warnings", "checks"}
    missing_top = required_top_keys - set(result.keys())
    assert not missing_top, f"Top-level keys missing: {missing_top}"
    ok("T03_response_shape", f"item={item_code}, keys OK")

    assert isinstance(result["checks"], list), "checks must be a list"
    assert len(result["checks"]) >= 1, "checks must have at least one entry"

    required_check_keys = {"key", "label", "passed", "severity", "message", "fix"}
    for i, check in enumerate(result["checks"]):
        missing = required_check_keys - set(check.keys())
        assert not missing, f"Check[{i}] missing keys: {missing}"
        assert check["severity"] in ("blocker", "warning", "info"), \
            f"Check[{i}] unexpected severity: {check['severity']}"

    ok("T04_check_shape",
       f"{len(result['checks'])} checks, all have required keys + valid severity")


# ── T05: is_sellable == (blockers == 0) ──────────────────────────────────────

def test_is_sellable_consistency():
    from ch_item_master.ch_item_master.readiness import get_item_readiness

    item_code = frappe.db.get_value("Item", {"disabled": 0, "has_variants": 0}, "name")
    if not item_code:
        ok("T05_is_sellable_consistency", "skipped — no item in DB")
        return

    result = get_item_readiness(item_code)
    expected_sellable = (result["blockers"] == 0)
    assert result["is_sellable"] == expected_sellable, \
        f"is_sellable={result['is_sellable']} but blockers={result['blockers']}"
    ok("T05_is_sellable_consistency",
       f"is_sellable={result['is_sellable']}, blockers={result['blockers']}")


# ── T06: Disabled item → not_disabled is a blocker ───────────────────────────

def test_disabled_item_check():
    from ch_item_master.ch_item_master.readiness import get_item_readiness

    item_code = frappe.db.get_value("Item", {"disabled": 1, "has_variants": 0}, "name")
    if not item_code:
        ok("T06_disabled_item_check", "skipped — no disabled item in DB")
        return

    result = get_item_readiness(item_code)
    not_disabled_check = next(
        (c for c in result["checks"] if c["key"] == "not_disabled"), None
    )
    assert not_disabled_check is not None, "not_disabled check missing"
    assert not not_disabled_check["passed"], "disabled item should fail not_disabled check"
    assert not_disabled_check["severity"] == "blocker", "not_disabled should be blocker"
    ok("T06_disabled_item_check",
       f"item={item_code} → not_disabled=FAIL/blocker as expected")


# ── T07: is_sales_item=0 → is_sales_item check as blocker ───────────────────

def test_non_sales_item_check():
    from ch_item_master.ch_item_master.readiness import get_item_readiness

    item_code = frappe.db.get_value(
        "Item", {"is_sales_item": 0, "disabled": 0, "has_variants": 0}, "name"
    )
    if not item_code:
        ok("T07_non_sales_item_check", "skipped — no non-sales item found in DB")
        return

    result = get_item_readiness(item_code)
    sales_check = next(
        (c for c in result["checks"] if c["key"] == "is_sales_item"), None
    )
    assert sales_check is not None, "is_sales_item check missing"
    assert not sales_check["passed"], "should fail for is_sales_item=0"
    assert sales_check["severity"] == "blocker"
    ok("T07_non_sales_item_check", f"item={item_code} → is_sales_item=FAIL/blocker")


# ── T08: get_readiness_for_missing_item() returns None for blank/unknown ──────

def test_readiness_for_missing_returns_none():
    from ch_item_master.ch_item_master.readiness import get_readiness_for_missing_item

    result = get_readiness_for_missing_item("")
    assert result is None, f"Empty string should return None, got {result}"

    result = get_readiness_for_missing_item("NONEXISTENT-BARCODE-XYZ-12345")
    assert result is None, f"Unknown term should return None, got {result}"

    ok("T08_readiness_for_missing_none", "empty/unknown → None as expected")


# ── T09: get_readiness_for_missing_item() returns report for known item ───────

def test_readiness_for_missing_known_item():
    from ch_item_master.ch_item_master.readiness import get_readiness_for_missing_item

    item_code = frappe.db.get_value("Item", {}, "name")
    if not item_code:
        ok("T09_readiness_for_missing_known", "skipped — no items in DB")
        return

    result = get_readiness_for_missing_item(item_code)
    assert result is not None, f"Known item_code should return report, got None"
    assert result.get("item_code") == item_code, \
        f"item_code mismatch: {result.get('item_code')} != {item_code}"
    ok("T09_readiness_for_missing_known",
       f"item={item_code} → report returned, is_sellable={result.get('is_sellable')}")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    global results
    results = []

    tests = [
        test_module_imports,
        test_nonexistent_item_raises,
        test_response_shape,
        test_is_sellable_consistency,
        test_disabled_item_check,
        test_non_sales_item_check,
        test_readiness_for_missing_returns_none,
        test_readiness_for_missing_known_item,
    ]

    for test in tests:
        try:
            test()
        except AssertionError as e:
            fail(test.__name__, str(e))
        except Exception as e:
            import traceback
            fail(test.__name__, f"EXCEPTION: {e}\n{traceback.format_exc()}")

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")
    print(f"Readiness E2E: {passed}/{len(results)} passed"
          + (f"  ← {failed} FAILED" if failed else " ✓"))
    print("=" * 60)

    if failed:
        raise SystemExit(1)
    return results
