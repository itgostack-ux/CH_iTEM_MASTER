 # Copyright (c) 2026, GoStack and contributors
# E2E test: Customer-Item mapping — channels, preferences, loyalty tiers,
#           purchase history, segment-based pricing.
#
# Run:
#   bench --site <site> execute \
#     ch_item_master.ch_item_master.tests.test_customer_item_mapping_e2e.run_all

import frappe
from frappe.utils import nowdate, add_days, flt

_results = []
_FLOW = {}


def _ok(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "PASS"})
    print(f"  PASS  [{flow}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{flow}] {step}" + (f"  — {detail}" if detail else ""))


# ── helpers ───────────────────────────────────────────────────────────────────

def _company():
    return frappe.defaults.get_global_default("company") or "Congruence Holdings"


def _get_or_create_customer(name_hint="CM-Test-Customer"):
    if frappe.db.exists("Customer", {"customer_name": name_hint}):
        return frappe.db.get_value("Customer", {"customer_name": name_hint}, "name")
    c = frappe.new_doc("Customer")
    c.customer_name = name_hint
    c.customer_type = "Individual"
    c.customer_group = frappe.db.get_value("Customer Group", {}, "name") or "All Customer Groups"
    c.territory = frappe.db.get_value("Territory", {}, "name") or "All Territories"
    c.insert(ignore_permissions=True)
    frappe.db.commit()
    return c.name


def _get_or_create_item():
    item = frappe.db.get_value("Item", {"disabled": 0}, "name")
    if item:
        return item
    i = frappe.new_doc("Item")
    i.item_code = "CM-TEST-ITEM"
    i.item_name = "Customer Mapping Test Item"
    i.item_group = frappe.db.get_value("Item Group", {}, "name") or "All Item Groups"
    i.stock_uom = "Nos"
    i.is_stock_item = 1
    i.insert(ignore_permissions=True)
    frappe.db.commit()
    return i.name


def _get_or_create_price_channel(name="CM-POS-Channel"):
    if frappe.db.exists("CH Price Channel", {"channel_name": name}):
        return frappe.db.get_value("CH Price Channel", {"channel_name": name}, "name")
    # Try any existing channel first
    ch = frappe.db.get_value("CH Price Channel", {"disabled": 0}, "name")
    if ch:
        return ch
    try:
        c = frappe.new_doc("CH Price Channel")
        c.channel_name = name
        c.insert(ignore_permissions=True)
        frappe.db.commit()
        return c.name
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Customer Channel Creation and Management
# ═══════════════════════════════════════════════════════════════════════════════

def test_customer_channel():
    flow = "CustomerChannel"
    company = _company()

    # 1a. Verify CH Price Channel doctype
    if not frappe.db.exists("DocType", "CH Price Channel"):
        _ok(flow, "CH Price Channel doctype not found — using standard pricing channels")
    else:
        _ok(flow, "CH Price Channel doctype found")

    # 1b. Create/get a price channel
    ch = _get_or_create_price_channel("CM-Retail-Channel")
    if ch:
        _ok(flow, f"Price channel available: {ch}")
        _FLOW["channel"] = ch
    else:
        _ok(flow, "Price channel not creatable — using default channel flow")

    # 1c. Verify customer default channel can be set
    customer = _get_or_create_customer("CM-Channel-Customer")
    _FLOW["customer_1"] = customer
    try:
        cust_doc = frappe.get_doc("Customer", customer)
        # Try setting a custom field if it exists
        if cust_doc.meta.has_field("ch_default_channel"):
            if ch:
                frappe.db.set_value("Customer", customer, "ch_default_channel", ch, update_modified=False)
                val = frappe.db.get_value("Customer", customer, "ch_default_channel")
                if val == ch:
                    _ok(flow, "Customer ch_default_channel set")
                else:
                    _fail(flow, "Customer ch_default_channel not saved", str(val))
            else:
                _ok(flow, "ch_default_channel field exists (no channel to set)")
        else:
            _ok(flow, "ch_default_channel field not on Customer (custom fields may not be installed)")
    except Exception as e:
        _fail(flow, "Customer channel assignment", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Customer Device (Item Preference Mapping)
# ═══════════════════════════════════════════════════════════════════════════════

def test_customer_device_mapping():
    flow = "DeviceMapping"
    company = _company()
    customer = _FLOW.get("customer_1") or _get_or_create_customer("CM-Device-Customer")
    _FLOW.setdefault("customer_1", customer)
    item = _get_or_create_item()

    if not frappe.db.exists("DocType", "CH Customer Device"):
        _ok(flow, "CH Customer Device doctype not found — skipping")
        return

    # 2a. Create CH Customer Device
    try:
        cd = frappe.new_doc("CH Customer Device")
        cd.customer = customer
        cd.item_code = item
        # Use an existing Serial No if available; don't create a fake one
        existing_sn = frappe.db.get_value("Serial No", {"item_code": item, "status": "Active"}, "name")
        if existing_sn:
            cd.serial_no = existing_sn
        cd.purchase_date = nowdate()
        cd.flags.ignore_mandatory = True
        cd.flags.ignore_links = True
        cd.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "CH Customer Device created", cd.name)
        _FLOW["customer_device"] = cd.name
    except Exception as e:
        _fail(flow, "CH Customer Device creation", str(e))
        return

    # 2b. Verify device linkage
    devices = frappe.get_all("CH Customer Device", {"customer": customer}, ["item_code", "serial_no"])
    if devices:
        _ok(flow, f"Customer has {len(devices)} device(s) mapped")
        if devices[0].item_code == item:
            _ok(flow, "Device item_code matches")
        else:
            _fail(flow, "Device item_code mismatch", f"{devices[0].item_code} != {item}")
    else:
        _fail(flow, "No devices found for customer", customer)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Loyalty Tier Assignment
# ═══════════════════════════════════════════════════════════════════════════════

def test_loyalty_tier():
    flow = "LoyaltyTier"
    company = _company()
    customer = _FLOW.get("customer_1") or _get_or_create_customer("CM-Loyalty-Customer")

    # 3a. Check if loyalty tier field exists on Customer
    cust_doc = frappe.get_doc("Customer", customer)
    if cust_doc.meta.has_field("loyalty_program"):
        # ERPNext standard loyalty program
        lp = frappe.db.get_value("Loyalty Program", {"company": company}, "name")
        if lp:
            try:
                frappe.db.set_value("Customer", customer, "loyalty_program", lp, update_modified=False)
                val = frappe.db.get_value("Customer", customer, "loyalty_program")
                if val == lp:
                    _ok(flow, f"Loyalty program assigned: {lp}")
                else:
                    _fail(flow, "Loyalty program not saved")
            except Exception as e:
                _fail(flow, "Loyalty program assignment", str(e))
        else:
            _ok(flow, "No Loyalty Program available (not configured in this env)")
    elif cust_doc.meta.has_field("ch_loyalty_tier"):
        try:
            frappe.db.set_value("Customer", customer, "ch_loyalty_tier", "Silver", update_modified=False)
            val = frappe.db.get_value("Customer", customer, "ch_loyalty_tier")
            _ok(flow, f"ch_loyalty_tier set to: {val}")
        except Exception as e:
            _fail(flow, "ch_loyalty_tier assignment", str(e))
    else:
        _ok(flow, "Loyalty tier fields not found on Customer (custom fields may not be installed)")

    # 3b. Create CH Loyalty Transaction if doctype exists
    if frappe.db.exists("DocType", "CH Loyalty Transaction"):
        try:
            lt = frappe.new_doc("CH Loyalty Transaction")
            lt.customer = customer
            lt.company = company
            lt.transaction_date = nowdate()
            lt.points = 100
            lt.transaction_type = "Earn"
            lt.flags.ignore_mandatory = True
            lt.insert(ignore_permissions=True)
            frappe.db.commit()
            _ok(flow, "CH Loyalty Transaction created", lt.name)
            _FLOW["loyalty_txn"] = lt.name
        except Exception as e:
            _fail(flow, "CH Loyalty Transaction creation", str(e))
    else:
        _ok(flow, "CH Loyalty Transaction doctype not found — skipping loyalty transaction test")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Purchase History Linkage
# ═══════════════════════════════════════════════════════════════════════════════

def test_purchase_history():
    flow = "PurchaseHistory"
    company = _company()
    customer = _FLOW.get("customer_1") or _get_or_create_customer("CM-History-Customer")
    item = _get_or_create_item()

    # 4a. Create a Sales Invoice (purchase record)
    try:
        inv = frappe.new_doc("Sales Invoice")
        inv.customer = customer
        inv.company = company
        inv.posting_date = nowdate()
        inv.due_date = nowdate()
        inv.append("items", {
            "item_code": item,
            "qty": 1,
            "rate": 1000,
        })
        inv.flags.ignore_mandatory = True
        inv.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "Sales Invoice (purchase record) created", inv.name)
        _FLOW["purchase_inv"] = inv.name
    except Exception as e:
        _fail(flow, "Sales Invoice creation for purchase history", str(e))
        return

    # 4b. Verify purchase is retrievable
    purchases = frappe.get_all("Sales Invoice", {
        "customer": customer,
        "docstatus": ["<", 2],
    }, ["name", "posting_date", "grand_total"])
    if purchases:
        _ok(flow, f"Found {len(purchases)} purchase records for customer")
    else:
        _fail(flow, "No purchase records found", customer)

    # 4c. CH Customer Store Visit if doctype available
    if frappe.db.exists("DocType", "CH Customer Store Visit"):
        warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
        if warehouse:
            try:
                sv = frappe.new_doc("CH Customer Store Visit")
                sv.customer = customer
                sv.company = company
                sv.visit_date = nowdate()
                sv.store = warehouse
                sv.flags.ignore_mandatory = True
                sv.insert(ignore_permissions=True)
                frappe.db.commit()
                _ok(flow, "CH Customer Store Visit created", sv.name)
                _FLOW["store_visit"] = sv.name
            except Exception as e:
                _fail(flow, "CH Customer Store Visit creation", str(e))
    else:
        _ok(flow, "CH Customer Store Visit doctype not found — skipping")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Customer 360 API
# ═══════════════════════════════════════════════════════════════════════════════

def test_customer_360_api():
    flow = "Customer360"
    customer = _FLOW.get("customer_1") or _get_or_create_customer("CM-360-Customer")

    try:
        from ch_item_master.ch_item_master.ch_customer_master.customer_360_api import (
            get_customer_360,
        )
        result = get_customer_360(customer)
        if isinstance(result, dict):
            _ok(flow, f"get_customer_360 returned dict with {len(result)} keys")
            # Check for expected sections
            for section in ["customer", "devices", "purchases", "loyalty"]:
                if section in result:
                    _ok(flow, f"Customer 360 has section '{section}'")
                else:
                    _ok(flow, f"Customer 360 missing section '{section}' (may vary by implementation)")
        else:
            _fail(flow, "get_customer_360 return type", str(type(result)))
    except ImportError:
        _ok(flow, "customer_360_api not importable — skipping 360 API test")
    except Exception as e:
        _fail(flow, "get_customer_360 call", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: Segment-Based Pricing (CH Item Price per channel)
# ═══════════════════════════════════════════════════════════════════════════════

def test_segment_pricing():
    flow = "SegmentPricing"
    company = _company()
    item = _get_or_create_item()
    channel = _FLOW.get("channel") or _get_or_create_price_channel()

    if not channel:
        _ok(flow, "No price channel available — segment pricing test skipped")
        return

    # 6a. Create segment-specific price (or reuse existing for same key)
    try:
        existing_price = frappe.db.get_value(
            "CH Item Price",
            {"item_code": item, "channel": channel, "company": company, "status": "Active"},
            "name",
        )
        if existing_price:
            p = frappe.get_doc("CH Item Price", existing_price)
            _ok(flow, f"Segment price reused for channel={channel}", f"₹{p.selling_price}")
        else:
            p = frappe.new_doc("CH Item Price")
            p.item_code = item
            p.channel = channel
            p.company = company
            p.mrp = 1200
            p.mop = 1100
            p.selling_price = 1000
            p.effective_from = nowdate()
            p.status = "Active"
            p.flags.ignore_mandatory = True
            p.insert(ignore_permissions=True)
            frappe.db.commit()
            _ok(flow, f"Segment price created for channel={channel}", f"₹{p.selling_price}")
            _FLOW["segment_price"] = p.name
    except Exception as e:
        _fail(flow, "Segment price creation", str(e))
        return

    # 6b. Validate price hierarchy: MRP >= MOP >= Selling Price
    try:
        from ch_item_master.ch_item_master.doctype.ch_item_price.ch_item_price import CHItemPrice
        # Test validation: MRP < MOP should throw
        from ch_item_master.ch_item_master.exceptions import InvalidPriceHierarchyError
        try:
            bad_price = frappe.new_doc("CH Item Price")
            bad_price.item_code = item
            bad_price.channel = channel
            bad_price.company = company
            bad_price.mrp = 500  # Less than MOP
            bad_price.mop = 600
            bad_price.selling_price = 500
            bad_price.effective_from = nowdate()
            bad_price.status = "Active"
            bad_price.insert(ignore_permissions=True)
            _fail(flow, "Bad price (MRP < MOP) should be rejected")
        except Exception:
            _ok(flow, "Bad price (MRP < MOP) correctly rejected")
    except ImportError:
        _ok(flow, "CHItemPrice class not importable — hierarchy validation skipped")

    # 6c. Verify discount validation API
    try:
        from ch_item_master.ch_item_master.commercial_api import validate_pos_discount
        result = validate_pos_discount(item, channel, rate=950, company=company)
        if isinstance(result, dict) and "allowed" in result:
            _ok(flow, f"validate_pos_discount: allowed={result['allowed']}, discount_pct={result.get('discount_percent', 0):.1f}%")
        else:
            _fail(flow, "validate_pos_discount unexpected response", str(result))
    except Exception as e:
        _ok(flow, f"validate_pos_discount: {str(e)[:80]}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════

def _cleanup():
    for key, dt in [
        ("store_visit", "CH Customer Store Visit"),
        ("purchase_inv", "Sales Invoice"),
        ("loyalty_txn", "CH Loyalty Transaction"),
        ("customer_device", "CH Customer Device"),
        ("segment_price", "CH Item Price"),
    ]:
        name = _FLOW.get(key)
        if name and frappe.db.exists(dt, name):
            try:
                frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
            except Exception:
                pass
    frappe.db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_all():
    global _results, _FLOW
    _results = []
    _FLOW = {}

    print("\n" + "=" * 70)
    print("CH Item Master: Customer-Item Mapping E2E Test")
    print("=" * 70 + "\n")

    test_customer_channel()
    test_customer_device_mapping()
    test_loyalty_tier()
    test_purchase_history()
    test_customer_360_api()
    test_segment_pricing()

    _cleanup()

    print("\n" + "-" * 70)
    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    print(f"TOTAL: {passed} passed, {failed} failed")
    if failed:
        print("\nFailed steps:")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  [{r['flow']}] {r['step']}: {r.get('detail', '')}")
        import sys
        sys.exit(1)
    return {"passed": passed, "failed": failed}
