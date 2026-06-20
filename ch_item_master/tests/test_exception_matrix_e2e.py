"""
CH Exception Approval Matrix — E2E Test Suite.

Verifies the matrix-driven exception routing (single source of truth =
CH Approval Authority) added on top of the legacy flat approval_level:

  • authority router resolves the correct value band → role
  • next-band-above walk (escalation ladder)
  • raise_exception routes a request to the matrix band + resolves an approver
  • SLA escalation moves a stale Pending request up one band

Defensive: every check SKIPs cleanly when ch_erp15 (the authority engine) is
not installed on the site.

Run:
    bench --site erpnext.local execute \
        ch_item_master.tests.test_exception_matrix_e2e.run_all
"""

import traceback

import frappe
from frappe.utils import now_datetime, add_to_date, flt

_results = []
FLOW = "ExceptionMatrix"

_ACTION = "Approve"
_DT = "CH Exception Request"

# (role, max_amount, priority) — overlapping-from-0 bands.
_LADDER = [
    ("CH Store Executive", 5000, 10),
    ("CH Area Sales Manager", 50000, 20),
    ("CH Zonal Sales Manager", 500000, 30),
    ("CH National Head", 0, 40),
]


def _ok(step, detail=""):
    _results.append({"step": step, "status": "PASS"})
    print(f"  PASS  [{FLOW}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(step, detail=""):
    _results.append({"step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{FLOW}] {step}" + (f"  — {detail}" if detail else ""))


def _skip(step, detail=""):
    _results.append({"step": step, "status": "SKIP"})
    print(f"  SKIP  [{FLOW}] {step}" + (f"  ({detail})" if detail else ""))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _authority():
    try:
        from ch_erp15.ch_erp15.auth import authority
        return authority
    except Exception:
        return None


def _ensure_role(role):
    if not frappe.db.exists("Role", role):
        r = frappe.new_doc("Role")
        r.role_name = role
        r.desk_access = 1
        r.flags.ignore_permissions = True
        r.insert(ignore_permissions=True)


def _ensure_matrix():
    """Ensure the exception ladder rows exist in CH Approval Authority."""
    for role, max_amt, priority in _LADDER:
        _ensure_role(role)
        if frappe.db.exists("CH Approval Authority",
                            {"doctype_target": _DT, "action": _ACTION, "role": role}):
            continue
        doc = frappe.new_doc("CH Approval Authority")
        doc.doctype_target = _DT
        doc.action = _ACTION
        doc.role = role
        doc.max_amount = max_amt
        doc.priority = priority
        doc.active = 1
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
    frappe.db.commit()


def _ensure_exception_type():
    name = "Matrix E2E Override"
    if not frappe.db.exists("CH Exception Type", name):
        doc = frappe.get_doc({
            "doctype": "CH Exception Type",
            "exception_type": name,
            "enabled": 1,
            "routing_mode": "Approval Matrix",
            "requires_otp": 0,
            "max_value_without_approval": 0,
            "validity_minutes": 30,
            "escalation_sla_minutes": 60,
            "applicable_to_ggr": 1,
            "applicable_to_gfs": 1,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    return name


def _any_company():
    return frappe.db.get_value("Company", {}, "name")


def _cleanup_request(name):
    if name and frappe.db.exists("CH Exception Request", name):
        try:
            doc = frappe.get_doc("CH Exception Request", name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc("CH Exception Request", name, force=True, ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            pass


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_01_router_resolves_band():
    """required_role_for_amount picks the least-senior covering band."""
    auth = _authority()
    if not auth:
        _skip("01 router resolves band", "ch_erp15 not installed")
        return
    try:
        _ensure_matrix()
        cases = [
            (3000, "CH Store Executive"),
            (30000, "CH Area Sales Manager"),
            (300000, "CH Zonal Sales Manager"),
            (5_000_000, "CH National Head"),
        ]
        for amount, expected in cases:
            got = auth.required_role_for_amount(_ACTION, _DT, amount)
            assert got == expected, f"amount {amount}: expected {expected}, got {got}"
        _ok("01 router resolves band", "4 bands correct")
    except Exception as e:
        _fail("01 router resolves band", str(e))


def test_02_next_band_above():
    """next_band_above walks the ladder upward for escalation."""
    auth = _authority()
    if not auth:
        _skip("02 next band above", "ch_erp15 not installed")
        return
    try:
        _ensure_matrix()
        assert auth.next_band_above(_ACTION, _DT, 5000) == "CH Area Sales Manager"
        assert auth.next_band_above(_ACTION, _DT, 50000) == "CH Zonal Sales Manager"
        assert auth.next_band_above(_ACTION, _DT, 500000) == "CH National Head"
        # top band reports unlimited (inf) — this is the scheduler's "nothing
        # above, leave for hard expiry" signal.
        assert auth.max_amount_for_role(_ACTION, _DT, "CH National Head") == float("inf")
        _ok("02 next band above", "ladder walk correct")
    except Exception as e:
        _fail("02 next band above", str(e))


def test_03_raise_routes_via_matrix():
    """raise_exception assigns the matrix band role for the request value."""
    if not _authority():
        _skip("03 raise routes via matrix", "ch_erp15 not installed")
        return
    name = None
    try:
        from ch_item_master.ch_item_master.exception_api import raise_exception
        _ensure_matrix()
        etype = _ensure_exception_type()
        company = _any_company()
        if not company:
            _skip("03 raise routes via matrix", "no Company on site")
            return
        frappe.set_user("Administrator")
        res = raise_exception(
            exception_type=etype,
            company=company,
            reason="Matrix routing e2e",
            requested_value=30000,  # Area Sales Manager band
        )
        name = res.get("name")
        assert res.get("status") == "Pending", f"expected Pending, got {res.get('status')}"
        assert res.get("approval_role") == "CH Area Sales Manager", \
            f"expected Area band, got {res.get('approval_role')}"
        _ok("03 raise routes via matrix", f"{name} → {res.get('approval_role')}")
    except Exception as e:
        _fail("03 raise routes via matrix", str(e))
    finally:
        _cleanup_request(name)


def test_04_sla_escalation_moves_up():
    """escalate_pending_exceptions bumps a stale request to the next band."""
    if not _authority():
        _skip("04 sla escalation", "ch_erp15 not installed")
        return
    name = None
    try:
        from ch_item_master.ch_item_master.exception_api import (
            raise_exception, escalate_pending_exceptions,
        )
        _ensure_matrix()
        etype = _ensure_exception_type()
        company = _any_company()
        if not company:
            _skip("04 sla escalation", "no Company on site")
            return
        frappe.set_user("Administrator")
        res = raise_exception(
            exception_type=etype,
            company=company,
            reason="SLA escalation e2e",
            requested_value=3000,  # Store Executive band
        )
        name = res.get("name")
        assert res.get("approval_role") == "CH Store Executive", \
            f"expected Store band, got {res.get('approval_role')}"

        # Backdate raised_at beyond the 60-min SLA so it is due for escalation.
        frappe.db.set_value("CH Exception Request", name, "raised_at",
                            add_to_date(now_datetime(), minutes=-120))
        frappe.db.commit()

        escalate_pending_exceptions()

        row = frappe.db.get_value("CH Exception Request", name,
                                  ["status", "approval_role"], as_dict=True)
        assert row.status == "Escalated", f"expected Escalated, got {row.status}"
        assert row.approval_role == "CH Area Sales Manager", \
            f"expected Area band after escalation, got {row.approval_role}"
        _ok("04 sla escalation", f"{name} Store → {row.approval_role}")
    except Exception as e:
        _fail("04 sla escalation", str(e))
    finally:
        _cleanup_request(name)


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    global _results
    _results = []

    print("\n" + "=" * 60)
    print("CH Exception Approval Matrix — E2E Tests")
    print("=" * 60 + "\n")

    frappe.set_user("Administrator")

    tests = [
        test_01_router_resolves_band,
        test_02_next_band_above,
        test_03_raise_routes_via_matrix,
        test_04_sla_escalation_moves_up,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            _fail(t.__name__, f"Unhandled: {e}")
            traceback.print_exc()
        try:
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")
    total = len(_results)

    print(f"\n{'='*60}")
    print(f"TOTAL: {passed} passed, {failed} failed, {skipped} skipped / {total}")
    if failed:
        print("\nFailed:")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  FAIL  [{FLOW}] {r['step']}: {r.get('detail','')}")
    print("=" * 60)

    if failed:
        raise Exception(f"Exception Matrix E2E: {failed} test(s) failed")
    return _results
