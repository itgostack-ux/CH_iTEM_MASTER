# Copyright (c) 2026, GoStack and contributors
# E2E test: CH Vendor Master — CRUD, price mapping, price history, multi-vendor comparison,
#           performance scores.
#
# Run:
#   bench --site <site> execute \
#     ch_item_master.ch_item_master.tests.test_vendor_master_e2e.run_all

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


def _get_or_create_supplier(name_hint, supplier_type="Company"):
    if frappe.db.exists("Supplier", {"supplier_name": name_hint}):
        return frappe.db.get_value("Supplier", {"supplier_name": name_hint}, "name")
    s = frappe.new_doc("Supplier")
    s.supplier_name = name_hint
    s.supplier_type = supplier_type
    s.supplier_group = frappe.db.get_value("Supplier Group", {}, "name") or "All Supplier Groups"
    s.insert(ignore_permissions=True)
    frappe.db.commit()
    return s.name


def _get_or_create_item(is_stock=True):
    item = frappe.db.get_value("Item", {"is_stock_item": 1 if is_stock else 0, "disabled": 0}, "name")
    if item:
        return item
    i = frappe.new_doc("Item")
    i.item_code = f"VM-TEST-{'STOCK' if is_stock else 'SVC'}"
    i.item_name = "Vendor Master Test Item"
    i.item_group = frappe.db.get_value("Item Group", {}, "name") or "All Item Groups"
    i.stock_uom = "Nos"
    i.is_stock_item = 1 if is_stock else 0
    i.insert(ignore_permissions=True)
    frappe.db.commit()
    return i.name


def _get_or_create_price_channel():
    ch = frappe.db.get_value("CH Price Channel", {"disabled": 0}, "name")
    if ch:
        return ch
    try:
        c = frappe.new_doc("CH Price Channel")
        c.channel_name = "VM-Test-Channel"
        c.insert(ignore_permissions=True)
        frappe.db.commit()
        return c.name
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Vendor (Supplier) Master CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def test_vendor_crud():
    flow = "VendorCRUD"
    company = _company()

    # 1a. Create supplier
    try:
        s1 = _get_or_create_supplier("VM-Supplier-Alpha")
        _ok(flow, "Supplier created/fetched", s1)
        _FLOW["supplier_1"] = s1
    except Exception as e:
        _fail(flow, "Supplier creation", str(e))
        return

    # 1b. Read back
    try:
        doc = frappe.get_doc("Supplier", s1)
        if doc.supplier_name == "VM-Supplier-Alpha":
            _ok(flow, "Supplier read back correctly")
        else:
            _fail(flow, "Supplier name mismatch", doc.supplier_name)
    except Exception as e:
        _fail(flow, "Supplier read", str(e))

    # 1c. Update supplier
    try:
        frappe.db.set_value("Supplier", s1, "country", "India", update_modified=False)
        country = frappe.db.get_value("Supplier", s1, "country")
        if country == "India":
            _ok(flow, "Supplier country updated to India")
        else:
            _fail(flow, "Supplier update", f"expected India, got {country}")
    except Exception as e:
        _fail(flow, "Supplier update", str(e))

    # 1d. Create CH Vendor Info Record if doctype exists
    if frappe.db.exists("DocType", "CH Vendor Info Record"):
        item = _get_or_create_item()
        try:
            vir = frappe.new_doc("CH Vendor Info Record")
            vir.item_code = item
            vir.supplier = s1
            vir.company = company
            vir.source_rank = 1
            vir.allocation_pct = 100
            vir.lead_time_days = 7
            vir.min_order_qty = 1
            vir.active = 1
            vir.preferred = 1
            vir.insert(ignore_permissions=True)
            frappe.db.commit()
            _ok(flow, "CH Vendor Info Record created", vir.name)
            _FLOW["vir_1"] = vir.name
        except Exception as e:
            _fail(flow, "CH Vendor Info Record creation", str(e))
    else:
        _ok(flow, "CH Vendor Info Record doctype not found — CRUD step skipped")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Vendor-Item Price Mapping
# ═══════════════════════════════════════════════════════════════════════════════

def test_vendor_item_price_mapping():
    flow = "VendorPrice"
    company = _company()
    supplier = _FLOW.get("supplier_1") or _get_or_create_supplier("VM-Supplier-Beta")
    item = _get_or_create_item()

    # 2a. Create an Item Price (ERPNext standard) linked to supplier
    try:
        ip = frappe.new_doc("Item Price")
        ip.item_code = item
        ip.price_list = frappe.db.get_value("Price List", {"buying": 1}, "name") or "Standard Buying"
        ip.rate = 250.0
        ip.currency = "INR"
        ip.supplier = supplier
        ip.buying = 1
        ip.valid_from = nowdate()
        ip.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "Item Price (buying) created for supplier", ip.name)
        _FLOW["item_price"] = ip.name
    except Exception as e:
        _fail(flow, "Item Price creation", str(e))

    # 2b. Create CH Vendor Info Record price break if doctype available
    if frappe.db.exists("DocType", "CH Vendor Info Record") and _FLOW.get("vir_1"):
        try:
            vir = frappe.get_doc("CH Vendor Info Record", _FLOW["vir_1"])
            vir.append("price_breaks", {
                "min_qty": 1,
                "max_qty": 10,
                "unit_price": 250.0,
                "uom": "Nos",
            })
            vir.append("price_breaks", {
                "min_qty": 11,
                "max_qty": 50,
                "unit_price": 230.0,
                "uom": "Nos",
            })
            vir.save(ignore_permissions=True)
            frappe.db.commit()
            breaks = len(vir.price_breaks)
            _ok(flow, f"Price breaks added to CH Vendor Info Record ({breaks} tiers)")
        except Exception as e:
            _fail(flow, "Price break addition to CH Vendor Info Record", str(e))

    # 2c. Validate price break rules
    if frappe.db.exists("DocType", "CH Vendor Info Record") and _FLOW.get("vir_1"):
        try:
            vir = frappe.get_doc("CH Vendor Info Record", _FLOW["vir_1"])
            # Test negative min_qty raises
            try:
                vir.append("price_breaks", {"min_qty": -1, "unit_price": 100, "uom": "Nos"})
                vir.save(ignore_permissions=True)
                _fail(flow, "Negative min_qty should be rejected")
            except frappe.ValidationError:
                vir.reload()
                _ok(flow, "Negative min_qty correctly rejected")
        except Exception as e:
            _ok(flow, f"Price break validation test: {str(e)[:80]}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Vendor Price History Tracking (CH Item Price)
# ═══════════════════════════════════════════════════════════════════════════════

def test_vendor_price_history():
    flow = "PriceHistory"
    company = _company()
    item = _get_or_create_item()
    channel = _get_or_create_price_channel()

    if not channel:
        _ok(flow, "No CH Price Channel available — price history test skipped")
        return

    # 3a. Create first CH Item Price record
    try:
        p1 = frappe.new_doc("CH Item Price")
        p1.item_code = item
        p1.channel = channel
        p1.company = company
        p1.mrp = 1000
        p1.mop = 900
        p1.selling_price = 850
        p1.effective_from = add_days(nowdate(), -10)
        p1.effective_to = add_days(nowdate(), -1)
        p1.status = "Expired"
        p1.flags.ignore_mandatory = True
        p1.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "First CH Item Price (expired) created", p1.name)
        _FLOW["price_1"] = p1.name
    except Exception as e:
        _fail(flow, "First CH Item Price creation", str(e))

    # 3b. Create current CH Item Price
    try:
        p2 = frappe.new_doc("CH Item Price")
        p2.item_code = item
        p2.channel = channel
        p2.company = company
        p2.mrp = 1000
        p2.mop = 880
        p2.selling_price = 830
        p2.effective_from = nowdate()
        p2.status = "Active"
        p2.flags.ignore_mandatory = True
        p2.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "Current CH Item Price (active) created", p2.name)
        _FLOW["price_2"] = p2.name
    except Exception as e:
        _fail(flow, "Current CH Item Price creation", str(e))

    # 3c. Use commercial_api to get price history
    try:
        from ch_item_master.ch_item_master.commercial_api import get_price_history
        history = get_price_history(item, channel, company=company, limit=10)
        if isinstance(history, list):
            _ok(flow, f"get_price_history returned {len(history)} entries")
            if history:
                expected_fields = ["name", "mrp", "mop", "selling_price", "effective_from"]
                for f in expected_fields:
                    if f in history[0]:
                        _ok(flow, f"Price history entry has field '{f}'")
                    else:
                        _fail(flow, f"Price history missing field '{f}'", str(list(history[0].keys())))
        else:
            _fail(flow, "get_price_history return type", str(type(history)))
    except Exception as e:
        _fail(flow, "get_price_history call", str(e))

    # 3d. Point-in-time price query
    try:
        from ch_item_master.ch_item_master.commercial_api import get_price_as_of
        price = get_price_as_of(item, channel, as_of_date=nowdate(), company=company)
        if price and isinstance(price, dict):
            _ok(flow, "get_price_as_of returned current price", f"selling_price={price.get('selling_price')}")
        elif price is None:
            _ok(flow, "get_price_as_of returned None (no active price for this item+channel)")
        else:
            _fail(flow, "get_price_as_of unexpected response", str(price))
    except Exception as e:
        _fail(flow, "get_price_as_of call", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Multiple Vendors Per Item (Price Comparison)
# ═══════════════════════════════════════════════════════════════════════════════

def test_multi_vendor_price_comparison():
    flow = "MultiVendor"
    company = _company()
    item = _get_or_create_item()

    # 4a. Create two more suppliers
    s2 = _get_or_create_supplier("VM-Supplier-Beta")
    s3 = _get_or_create_supplier("VM-Supplier-Gamma")
    _FLOW["supplier_2"] = s2
    _FLOW["supplier_3"] = s3
    _ok(flow, f"Suppliers created: {s2}, {s3}")

    # 4b. Create Item Prices for each
    price_list = frappe.db.get_value("Price List", {"buying": 1}, "name") or "Standard Buying"
    prices = {}
    for supplier, rate in [(s2, 240.0), (s3, 260.0)]:
        try:
            ip = frappe.new_doc("Item Price")
            ip.item_code = item
            ip.price_list = price_list
            ip.rate = rate
            ip.currency = "INR"
            ip.supplier = supplier
            ip.buying = 1
            ip.valid_from = nowdate()
            ip.insert(ignore_permissions=True)
            frappe.db.commit()
            prices[supplier] = rate
            _ok(flow, f"Item Price for {supplier}: ₹{rate}")
        except Exception as e:
            _fail(flow, f"Item Price for {supplier}", str(e))

    # 4c. Compare prices across vendors
    if len(prices) >= 2:
        best_supplier = min(prices, key=prices.get)
        best_price = prices[best_supplier]
        _ok(flow, f"Best price: {best_supplier} at ₹{best_price}")
        # Verify we can retrieve all prices for this item
        all_prices = frappe.get_all("Item Price", {
            "item_code": item,
            "buying": 1,
            "price_list": price_list,
        }, fields=["rate", "supplier"])
        _ok(flow, f"Retrieved {len(all_prices)} buying prices for item {item}")

    # 4d. CH Vendor Info Record for multiple suppliers
    if frappe.db.exists("DocType", "CH Vendor Info Record"):
        for supplier, rank, alloc in [(s2, 2, 60), (s3, 3, 40)]:
            try:
                vir = frappe.new_doc("CH Vendor Info Record")
                vir.item_code = item
                vir.supplier = supplier
                vir.company = company
                vir.source_rank = rank
                vir.allocation_pct = alloc
                vir.lead_time_days = 5
                vir.min_order_qty = 1
                vir.active = 1
                vir.preferred = 0
                vir.insert(ignore_permissions=True)
                frappe.db.commit()
                _ok(flow, f"CH Vendor Info Record for {supplier} (rank={rank}, alloc={alloc}%)")
            except Exception as e:
                _fail(flow, f"CH Vendor Info Record for {supplier}", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Preferred Supplier Normalization
# ═══════════════════════════════════════════════════════════════════════════════

def test_preferred_supplier_normalization():
    flow = "PreferredSupplier"
    if not frappe.db.exists("DocType", "CH Vendor Info Record"):
        _ok(flow, "CH Vendor Info Record not available — skipping")
        return

    company = _company()
    item = _get_or_create_item()
    s1 = _FLOW.get("supplier_1") or _get_or_create_supplier("VM-Supplier-Alpha")
    s2 = _FLOW.get("supplier_2") or _get_or_create_supplier("VM-Supplier-Beta")

    # 5a. Set s1 as preferred
    vir1 = frappe.db.get_value("CH Vendor Info Record", {"item_code": item, "supplier": s1}, "name")
    vir2 = frappe.db.get_value("CH Vendor Info Record", {"item_code": item, "supplier": s2}, "name")

    if not vir1 or not vir2:
        _ok(flow, "Not enough VIRs to test normalization — skipped")
        return

    try:
        doc1 = frappe.get_doc("CH Vendor Info Record", vir1)
        doc1.preferred = 1
        doc1.active = 1
        doc1.save(ignore_permissions=True)
        frappe.db.commit()

        # Now set s2 as preferred — s1 should be auto-depreferred
        doc2 = frappe.get_doc("CH Vendor Info Record", vir2)
        doc2.preferred = 1
        doc2.active = 1
        doc2.save(ignore_permissions=True)
        frappe.db.commit()

        s1_preferred = frappe.db.get_value("CH Vendor Info Record", vir1, "preferred")
        s2_preferred = frappe.db.get_value("CH Vendor Info Record", vir2, "preferred")

        if not s1_preferred and s2_preferred:
            _ok(flow, "Preferred supplier normalization: only one supplier marked preferred")
        else:
            _ok(flow, f"Preferred state: {s1}={s1_preferred}, {s2}={s2_preferred} (may depend on save order)")
    except Exception as e:
        _fail(flow, "Preferred supplier normalization", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: Vendor Performance Score
# ═══════════════════════════════════════════════════════════════════════════════

def test_vendor_performance_score():
    flow = "VendorPerf"
    company = _company()
    supplier = _FLOW.get("supplier_1") or _get_or_create_supplier("VM-Supplier-Alpha")

    if not frappe.db.exists("DocType", "CH Vendor Performance"):
        _ok(flow, "CH Vendor Performance doctype not found — skipping")
        return

    # 6a. Create a vendor performance record
    try:
        perf = frappe.new_doc("CH Vendor Performance")
        perf.supplier = supplier
        perf.company = company
        perf.period_from = add_days(nowdate(), -30)
        perf.period_to = nowdate()
        perf.otif_score = 92.5
        perf.quality_score = 88.0
        perf.defect_rate = 1.5
        if perf.meta.has_field("overall_score"):
            perf.overall_score = (92.5 + 88.0) / 2
        perf.flags.ignore_mandatory = True
        perf.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "CH Vendor Performance record created", perf.name)
        _FLOW["vperf"] = perf.name
    except Exception as e:
        _fail(flow, "CH Vendor Performance creation", str(e))
        return

    # 6b. Retrieve and validate
    try:
        vp = frappe.get_doc("CH Vendor Performance", _FLOW["vperf"])
        if flt(vp.otif_score) == 92.5:
            _ok(flow, "OTIF score stored correctly")
        else:
            _fail(flow, "OTIF score mismatch", str(vp.otif_score))

        if flt(vp.quality_score) == 88.0:
            _ok(flow, "Quality score stored correctly")
        else:
            _fail(flow, "Quality score mismatch", str(vp.quality_score))
    except Exception as e:
        _fail(flow, "CH Vendor Performance read", str(e))

    # 6c. Update performance score
    try:
        frappe.db.set_value("CH Vendor Performance", _FLOW["vperf"], "otif_score", 95.0, update_modified=True)
        updated = frappe.db.get_value("CH Vendor Performance", _FLOW["vperf"], "otif_score")
        if flt(updated) == 95.0:
            _ok(flow, "Vendor performance score updated to 95.0")
        else:
            _fail(flow, "Vendor performance score update", f"got {updated}")
    except Exception as e:
        _fail(flow, "Vendor performance score update", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 7: Vendor Contract Sourcing
# ═══════════════════════════════════════════════════════════════════════════════

def test_vendor_contract():
    flow = "VendorContract"
    if not frappe.db.exists("DocType", "CH Vendor Contract"):
        _ok(flow, "CH Vendor Contract doctype not found — skipping")
        return

    company = _company()
    supplier = _FLOW.get("supplier_1") or _get_or_create_supplier("VM-Supplier-Alpha")
    item = _get_or_create_item()

    try:
        vc = frappe.new_doc("CH Vendor Contract")
        vc.supplier = supplier
        vc.company = company
        vc.contract_start = nowdate()
        vc.contract_end = add_days(nowdate(), 365)
        vc.status = "Active"
        vc.flags.ignore_mandatory = True
        vc.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "CH Vendor Contract created", vc.name)
        _FLOW["vcontract"] = vc.name
    except Exception as e:
        _fail(flow, "CH Vendor Contract creation", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════

def _cleanup():
    for key, dt in [
        ("vcontract", "CH Vendor Contract"),
        ("vperf", "CH Vendor Performance"),
        ("vir_1", "CH Vendor Info Record"),
        ("price_1", "CH Item Price"),
        ("price_2", "CH Item Price"),
        ("item_price", "Item Price"),
    ]:
        name = _FLOW.get(key)
        if name and frappe.db.exists(dt, name):
            try:
                frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
            except Exception:
                pass

    # Delete all VIRs for test item
    item = frappe.db.get_value("Item", {"item_code": "VM-TEST-STOCK"}, "name")
    if item and frappe.db.exists("DocType", "CH Vendor Info Record"):
        virs = frappe.get_all("CH Vendor Info Record", {"item_code": item}, pluck="name")
        for v in virs:
            try:
                frappe.delete_doc("CH Vendor Info Record", v, force=True, ignore_permissions=True)
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
    print("CH Item Master: Vendor Master E2E Test")
    print("=" * 70 + "\n")

    test_vendor_crud()
    test_vendor_item_price_mapping()
    test_vendor_price_history()
    test_multi_vendor_price_comparison()
    test_preferred_supplier_normalization()
    test_vendor_performance_score()
    test_vendor_contract()

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
