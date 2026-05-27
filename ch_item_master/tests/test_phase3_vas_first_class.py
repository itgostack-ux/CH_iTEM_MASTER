"""Phase 3 smoke test - VAS first-class layer.

Run:
	bench --site erpnext.local execute ch_item_master.tests.test_phase3_vas_first_class.run
"""

import frappe


def _must(label, ok, detail=""):
	prefix = "[PASS]" if ok else "[FAIL]"
	print(f"  {prefix} {label}{(' - ' + detail) if detail else ''}")
	if not ok:
		raise AssertionError(label)


def run():
	print("Phase 3 - VAS First-Class Smoke")

	for dt in [
		"VAS Product",
		"VAS Plan",
		"VAS Attach Rule",
		"VAS Claim",
		"VAS Commission",
		"VAS Partner",
	]:
		_must(f"DocType exists: {dt}", bool(frappe.db.exists("DocType", dt)))

	from ch_item_master.ch_item_master.vas.api import get_vas_plan_catalog, get_vas_claims
	plans = get_vas_plan_catalog(limit=5)
	claims = get_vas_claims(limit=5)
	_must("VAS plan catalog API works", isinstance(plans, list))
	_must("VAS claim API works", isinstance(claims, list))

	from ch_pos.pos_core.report.vas_attach_rate_by_store_cashier_category_day.vas_attach_rate_by_store_cashier_category_day import execute as vas_attach_execute
	cols, data = vas_attach_execute({"from_date": frappe.utils.nowdate(), "to_date": frappe.utils.nowdate()})
	_must("VAS attach report columns", isinstance(cols, list) and any(c.get("fieldname") == "attach_rate" for c in cols))
	_must("VAS attach report data shape", isinstance(data, list))

	print("Phase 3 - ALL PASS")
