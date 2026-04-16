"""
Smoke tests for supplier-specific pricing visibility.

Run:
  bench --site erpnext.local execute ch_item_master.tests.test_supplier_price_gap_e2e.run_all
"""

import frappe

results = []


def ok(name, detail=""):
    results.append(("PASS", name, detail))
    print(f"PASS  {name}{f'  ({detail})' if detail else ''}")


def fail(name, detail=""):
    results.append(("FAIL", name, detail))
    print(f"FAIL  {name}{f'  ({detail})' if detail else ''}")


def test_supplier_price_comparison_report():
    from ch_item_master.ch_item_master.report.supplier_price_comparison.supplier_price_comparison import execute

    cols, data = execute({})
    fieldnames = {c.get("fieldname") for c in cols if isinstance(c, dict)}
    if "supplier" in fieldnames and "last_purchase_rate" in fieldnames:
        ok("supplier_price_comparison_report", f"columns={len(cols)} rows={len(data)}")
    else:
        fail("supplier_price_comparison_report", f"fieldnames={sorted(fieldnames)}")


def run_all():
    print("\n=== SUPPLIER PRICE GAP SMOKE TESTS ===")
    try:
        test_supplier_price_comparison_report()
    except Exception as e:
        fail("supplier_price_comparison_report", str(e))

    passed = len([r for r in results if r[0] == "PASS"])
    failed = len([r for r in results if r[0] == "FAIL"])
    print(f"\nSummary: PASS={passed} FAIL={failed}")
    return {"passed": passed, "failed": failed, "results": results}
