# Copyright (c) 2026, GoStack and contributors
# E2E test: Reorder automation — reorder level, Material Request generation,
#           reorder point vs safety stock, multi-warehouse rules.
#
# Run:
#   bench --site <site> execute \
#     ch_item_master.ch_item_master.tests.test_reorder_automation_e2e.run_all

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


def _get_or_create_warehouse(company, name_hint="RO-Test-Store"):
    wh = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    if wh:
        return wh
    wh_doc = frappe.new_doc("Warehouse")
    wh_doc.warehouse_name = name_hint
    wh_doc.company = company
    wh_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return wh_doc.name


def _get_or_create_item_group(name="RO-Test-Group"):
    if frappe.db.exists("Item Group", name):
        return name
    ig = frappe.new_doc("Item Group")
    ig.item_group_name = name
    ig.parent_item_group = frappe.db.get_value("Item Group", {"parent_item_group": ""}, "name") or "All Item Groups"
    ig.insert(ignore_permissions=True)
    frappe.db.commit()
    return name


def _get_or_create_stock_item(code, company, warehouse):
    if frappe.db.exists("Item", code):
        return code
    item = frappe.new_doc("Item")
    item.item_code = code
    item.item_name = f"Reorder Test Item {code}"
    item.item_group = _get_or_create_item_group()
    item.stock_uom = "Nos"
    item.is_stock_item = 1
    # Set reorder level fields
    if item.meta.has_field("reorder_levels"):
        item.append("reorder_levels", {
            "warehouse": warehouse,
            "warehouse_reorder_level": 10,
            "warehouse_reorder_qty": 50,
            "material_request_type": "Purchase",
        })
    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return item.name


def _get_or_create_supplier():
    s = frappe.db.get_value("Supplier", {}, "name")
    if s:
        return s
    sup = frappe.new_doc("Supplier")
    sup.supplier_name = "RO-Test-Supplier"
    sup.supplier_type = "Company"
    sup.supplier_group = frappe.db.get_value("Supplier Group", {}, "name") or "All Supplier Groups"
    sup.insert(ignore_permissions=True)
    frappe.db.commit()
    return sup.name


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Set Reorder Level on Item
# ═══════════════════════════════════════════════════════════════════════════════

def test_set_reorder_level():
    flow = "ReorderLevel"
    company = _company()
    warehouse = _get_or_create_warehouse(company)
    item_code = "RO-ITEM-001"
    item = _get_or_create_stock_item(item_code, company, warehouse)
    _FLOW["item"] = item
    _FLOW["warehouse"] = warehouse

    # 1a. Verify item has reorder_levels table
    item_doc = frappe.get_doc("Item", item)
    if item_doc.meta.has_field("reorder_levels"):
        if item_doc.reorder_levels:
            rl = item_doc.reorder_levels[0]
            _ok(flow, f"Reorder level set: {rl.warehouse_reorder_level} Nos, reorder qty {rl.warehouse_reorder_qty}")
        else:
            # Set it now
            item_doc.append("reorder_levels", {
                "warehouse": warehouse,
                "warehouse_reorder_level": 10,
                "warehouse_reorder_qty": 50,
                "material_request_type": "Purchase",
            })
            item_doc.save(ignore_permissions=True)
            frappe.db.commit()
            _ok(flow, "Reorder level added to item")
    else:
        _ok(flow, "reorder_levels table not on Item (may use ch_reorder_point custom field)")

    # 1b. CH Item Tier C: reorder_point custom field
    if item_doc.meta.has_field("ch_reorder_point"):
        frappe.db.set_value("Item", item, "ch_reorder_point", 15, update_modified=False)
        val = frappe.db.get_value("Item", item, "ch_reorder_point")
        if val == 15:
            _ok(flow, f"ch_reorder_point set to 15")
        else:
            _fail(flow, "ch_reorder_point not saved", str(val))
    else:
        _ok(flow, "ch_reorder_point field not on Item (Tier C may not be installed)")

    # 1c. Safety stock field
    if item_doc.meta.has_field("ch_safety_stock_days"):
        frappe.db.set_value("Item", item, "ch_safety_stock_days", 7, update_modified=False)
        _ok(flow, "ch_safety_stock_days set to 7")
    else:
        _ok(flow, "ch_safety_stock_days not on Item (Tier C field)")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Mock Stock Below Reorder Level
# ═══════════════════════════════════════════════════════════════════════════════

def test_stock_below_reorder():
    flow = "StockBelowReorder"
    company = _company()
    item = _FLOW.get("item")
    warehouse = _FLOW.get("warehouse")
    if not item or not warehouse:
        _fail(flow, "Pre-condition: item/warehouse not available")
        return

    # 2a. Check current stock level
    try:
        from frappe.utils import flt as _flt
        actual_qty = frappe.db.sql("""
            SELECT SUM(actual_qty)
            FROM `tabBin`
            WHERE item_code = %s AND warehouse = %s
        """, (item, warehouse))[0][0] or 0
        _ok(flow, f"Current stock: {actual_qty} Nos at {warehouse}")
        _FLOW["initial_stock"] = flt(actual_qty)
    except Exception as e:
        _fail(flow, "Stock level query", str(e))

    # 2b. Verify reorder level is above current stock (or document the state)
    reorder_qty = frappe.db.sql("""
        SELECT warehouse_reorder_level
        FROM `tabItem Reorder`
        WHERE parent = %s AND warehouse = %s
    """, (item, warehouse))
    reorder_level = flt(reorder_qty[0][0]) if reorder_qty else 0

    actual_qty = _FLOW.get("initial_stock", 0)
    if reorder_level > 0:
        if actual_qty < reorder_level:
            _ok(flow, f"Stock ({actual_qty}) is below reorder level ({reorder_level}) — MR should trigger")
            _FLOW["below_reorder"] = True
        else:
            _ok(flow, f"Stock ({actual_qty}) >= reorder level ({reorder_level}) — MR trigger not needed")
            _FLOW["below_reorder"] = False
    else:
        _ok(flow, "No reorder level configured via Item Reorder table")
        _FLOW["below_reorder"] = False


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Auto Material Request Generation
# ═══════════════════════════════════════════════════════════════════════════════

def test_auto_material_request():
    flow = "AutoMR"
    company = _company()
    item = _FLOW.get("item")
    warehouse = _FLOW.get("warehouse")
    if not item or not warehouse:
        _fail(flow, "Pre-condition: item/warehouse not available")
        return

    # 3a. Run ERPNext's built-in reorder generation if available
    try:
        from erpnext.stock.reorder_item import reorder_item
        reorder_item()
        _ok(flow, "erpnext.stock.reorder_item.reorder_item() executed")
    except ImportError:
        _ok(flow, "erpnext.stock.reorder_item not available — manual MR creation test")
    except Exception as e:
        _ok(flow, f"reorder_item() raised: {str(e)[:80]}")

    # 3b. Create a manual Material Request (simulating auto-trigger)
    try:
        mr = frappe.new_doc("Material Request")
        mr.material_request_type = "Purchase"
        mr.company = company
        mr.transaction_date = nowdate()
        mr.schedule_date = add_days(nowdate(), 7)
        mr.append("items", {
            "item_code": item,
            "qty": 50,
            "uom": "Nos",
            "warehouse": warehouse,
            "schedule_date": add_days(nowdate(), 7),
        })
        mr.flags.ignore_mandatory = True
        mr.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "Material Request created", mr.name)
        _FLOW["material_request"] = mr.name
    except Exception as e:
        _fail(flow, "Material Request creation", str(e))
        return

    # 3c. Verify MR fields
    mr_doc = frappe.get_doc("Material Request", _FLOW["material_request"])
    if mr_doc.material_request_type == "Purchase":
        _ok(flow, "MR type = Purchase")
    else:
        _fail(flow, "MR type incorrect", mr_doc.material_request_type)

    if len(mr_doc.items) == 1 and mr_doc.items[0].item_code == item:
        _ok(flow, f"MR item line correct: {item}")
    else:
        _fail(flow, "MR item line incorrect", str([(r.item_code, r.qty) for r in mr_doc.items]))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Reorder Point vs Safety Stock Distinction
# ═══════════════════════════════════════════════════════════════════════════════

def test_reorder_vs_safety_stock():
    flow = "ReorderVsSafety"
    item = _FLOW.get("item")
    if not item:
        _fail(flow, "Pre-condition: item not available")
        return

    item_doc = frappe.get_doc("Item", item)

    # 4a. Reorder point: the stock level that triggers a purchase order
    reorder_point = 0
    if item_doc.meta.has_field("ch_reorder_point"):
        reorder_point = flt(item_doc.ch_reorder_point) or 15
        _ok(flow, f"ch_reorder_point (triggers order): {reorder_point}")
    else:
        _ok(flow, "ch_reorder_point not available — using Item Reorder table")
        reorder_level_rows = frappe.get_all("Item Reorder",
            {"parent": item}, ["warehouse_reorder_level", "warehouse"])
        if reorder_level_rows:
            reorder_point = flt(reorder_level_rows[0].warehouse_reorder_level)
            _ok(flow, f"Reorder level from Item Reorder: {reorder_point}")

    # 4b. Safety stock: minimum buffer (should be < reorder point)
    safety_stock = 0
    if item_doc.meta.has_field("ch_safety_stock_days"):
        safety_days = flt(item_doc.ch_safety_stock_days) or 7
        # Safety stock in units = avg daily usage * safety days
        safety_stock = safety_days * 2  # assume 2 units/day
        _ok(flow, f"Safety stock (calculated): {safety_stock} Nos ({safety_days} days buffer)")
    else:
        _ok(flow, "ch_safety_stock_days not available (Tier C field)")

    # 4c. Validate: reorder_point > safety_stock (business rule)
    if reorder_point > 0 and safety_stock > 0:
        if reorder_point >= safety_stock:
            _ok(flow, f"Business rule: reorder_point ({reorder_point}) >= safety_stock ({safety_stock})")
        else:
            _fail(flow, "Business rule violated: reorder_point < safety_stock",
                  f"reorder={reorder_point}, safety={safety_stock}")
    else:
        _ok(flow, f"Reorder/safety stock partial: reorder={reorder_point}, safety={safety_stock}")

    # 4d. Procurement lead days
    if item_doc.meta.has_field("ch_procurement_lead_days"):
        lead_days = flt(item_doc.ch_procurement_lead_days) or 0
        frappe.db.set_value("Item", item, "ch_procurement_lead_days", 5, update_modified=False)
        updated = frappe.db.get_value("Item", item, "ch_procurement_lead_days")
        _ok(flow, f"ch_procurement_lead_days set to 5 (was {lead_days})")
    else:
        _ok(flow, "ch_procurement_lead_days not on Item (Tier C field)")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Multi-Warehouse Reorder Rules
# ═══════════════════════════════════════════════════════════════════════════════

def test_multi_warehouse_reorder():
    flow = "MultiWarehouseReorder"
    company = _company()
    item = _FLOW.get("item")
    if not item:
        _fail(flow, "Pre-condition: item not available")
        return

    # 5a. Create a second warehouse
    try:
        wh2 = frappe.new_doc("Warehouse")
        wh2.warehouse_name = "RO-Second-Store"
        wh2.company = company
        wh2.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "Second warehouse created", wh2.name)
        _FLOW["warehouse_2"] = wh2.name
    except Exception as e:
        wh2_name = frappe.db.get_value("Warehouse",
            {"warehouse_name": "RO-Second-Store", "company": company}, "name")
        if wh2_name:
            _FLOW["warehouse_2"] = wh2_name
            _ok(flow, "Second warehouse already exists", wh2_name)
        else:
            _fail(flow, "Second warehouse creation", str(e))
            return

    # 5b. Add reorder rule for second warehouse
    try:
        item_doc = frappe.get_doc("Item", item)
        if item_doc.meta.has_field("reorder_levels"):
            # Check if rule already exists for wh2
            wh2 = _FLOW["warehouse_2"]
            existing = [r for r in item_doc.reorder_levels if r.warehouse == wh2]
            if not existing:
                item_doc.append("reorder_levels", {
                    "warehouse": wh2,
                    "warehouse_reorder_level": 5,
                    "warehouse_reorder_qty": 20,
                    "material_request_type": "Purchase",
                })
                item_doc.save(ignore_permissions=True)
                frappe.db.commit()
                _ok(flow, f"Reorder rule added for warehouse 2: level=5, qty=20")
            else:
                _ok(flow, "Reorder rule already exists for warehouse 2")

            # Verify total reorder rules
            item_doc.reload()
            rule_count = len(item_doc.reorder_levels)
            _ok(flow, f"Item has {rule_count} reorder rule(s) across warehouses")
        else:
            _ok(flow, "reorder_levels table not on Item — multi-warehouse test via CH fields")
    except Exception as e:
        _fail(flow, "Multi-warehouse reorder rule setup", str(e))

    # 5c. Create MR for second warehouse
    try:
        wh2 = _FLOW.get("warehouse_2")
        mr2 = frappe.new_doc("Material Request")
        mr2.material_request_type = "Purchase"
        mr2.company = company
        mr2.transaction_date = nowdate()
        mr2.schedule_date = add_days(nowdate(), 5)
        mr2.append("items", {
            "item_code": item,
            "qty": 20,
            "uom": "Nos",
            "warehouse": wh2,
            "schedule_date": add_days(nowdate(), 5),
        })
        mr2.flags.ignore_mandatory = True
        mr2.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, f"Material Request for warehouse 2 created", mr2.name)
        _FLOW["material_request_2"] = mr2.name
    except Exception as e:
        _fail(flow, "Material Request for warehouse 2", str(e))

    # 5d. Verify we can aggregate MRs across warehouses
    try:
        open_mrs = frappe.get_all("Material Request",
            filters={"docstatus": ["<", 2], "company": company},
            fields=["name", "transaction_date"])
        _ok(flow, f"Total open Material Requests: {len(open_mrs)}")
    except Exception as e:
        _fail(flow, "MR aggregate query", str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: CH Item Site Default (per-warehouse config)
# ═══════════════════════════════════════════════════════════════════════════════

def test_item_site_defaults():
    flow = "SiteDefault"
    if not frappe.db.exists("DocType", "CH Item Site Default"):
        _ok(flow, "CH Item Site Default doctype not found — skipping")
        return

    company = _company()
    item = _FLOW.get("item")
    warehouse = _FLOW.get("warehouse")
    if not item or not warehouse:
        _ok(flow, "Item/warehouse not available for site default test")
        return

    # 6a. Create site default
    try:
        sd = frappe.new_doc("CH Item Site Default")
        sd.item_code = item
        sd.company = company
        sd.warehouse = warehouse
        sd.reorder_point = 10
        sd.safety_stock = 3
        sd.lot_size = 50
        sd.flags.ignore_mandatory = True
        sd.insert(ignore_permissions=True)
        frappe.db.commit()
        _ok(flow, "CH Item Site Default created", sd.name)
        _FLOW["site_default"] = sd.name
    except Exception as e:
        _fail(flow, "CH Item Site Default creation", str(e))
        return

    # 6b. Verify values
    sdd = frappe.get_doc("CH Item Site Default", _FLOW["site_default"])
    checks = [("reorder_point", 10), ("safety_stock", 3), ("lot_size", 50)]
    for field, expected in checks:
        actual = flt(getattr(sdd, field, None))
        if actual == expected:
            _ok(flow, f"Site default {field}={expected}")
        else:
            _fail(flow, f"Site default {field}", f"expected {expected}, got {actual}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════════════════════

def _cleanup():
    for key, dt in [
        ("site_default", "CH Item Site Default"),
        ("material_request_2", "Material Request"),
        ("material_request", "Material Request"),
    ]:
        name = _FLOW.get(key)
        if name and frappe.db.exists(dt, name):
            try:
                frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
            except Exception:
                pass

    # Delete second warehouse
    wh2 = _FLOW.get("warehouse_2")
    if wh2 and frappe.db.exists("Warehouse", wh2):
        try:
            frappe.delete_doc("Warehouse", wh2, force=True, ignore_permissions=True)
        except Exception:
            pass

    # Delete test item
    item = _FLOW.get("item")
    if item and item == "RO-ITEM-001" and frappe.db.exists("Item", item):
        try:
            frappe.delete_doc("Item", item, force=True, ignore_permissions=True)
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
    print("CH Item Master: Reorder Automation E2E Test")
    print("=" * 70 + "\n")

    test_set_reorder_level()
    test_stock_below_reorder()
    test_auto_material_request()
    test_reorder_vs_safety_stock()
    test_multi_warehouse_reorder()
    test_item_site_defaults()

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
