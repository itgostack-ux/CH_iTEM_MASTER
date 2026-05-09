# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Tier C Oracle-parity E2E tests.

Features covered:
  01-03: Item Revision / Version Control
  04-06: Formal Model Approval Gate
  07-09: GTIN / EAN / UPC validation + Trading Partner Aliases
  10-12: MRP / Coverage Planning fields + Site-Level Defaults
  13-15: Vendor Info-Record sourcing master
  16-20: Full PLM State Machine (valid transitions, invalid transitions,
          transaction blocking, EOL warning, Discontinued PO block)

Run:  bench --site erpnext.local run-tests --module ch_item_master.tests.test_tier_c_oracle_parity_e2e
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import today, add_days


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

TEST_HSN = "01011010"  # 8-digit HSN that exists in every ERPNext install
_TC_PREFIX = "TCC1"    # unique sub-category prefix for this test module (Tier C)
_TEST_CAT = "_TierC_Test_Category"
_TEST_SC: str | None = None  # resolved lazily


def _ensure_test_sub_category(prefix=_TC_PREFIX) -> str:
	"""Return a CH Sub Category name for tests (creates category + sub-cat if needed)."""
	global _TEST_SC
	if _TEST_SC:
		return _TEST_SC

	# Ensure parent category
	if not frappe.db.exists("CH Category", _TEST_CAT):
		cat = frappe.get_doc({
			"doctype": "CH Category",
			"category_name": _TEST_CAT,
			"item_group": "All Item Groups",
			"lifecycle_status": "Active",
		})
		cat.insert(ignore_permissions=True)

	# Find or create the sub-category
	sc_name = frappe.db.get_value("CH Sub Category", {"category": _TEST_CAT}, "name")
	if not sc_name:
		sc = frappe.get_doc({
			"doctype": "CH Sub Category",
			"sub_category_name": "_TierC_SC",
			"category": _TEST_CAT,
			"prefix": prefix,
			"item_nature": "Simple Auto-Named",
			"lifecycle_status": "Active",
		})
		sc.insert(ignore_permissions=True)
		sc_name = sc.name

	_TEST_SC = sc_name
	return sc_name


def _make_item(item_code: str, extra: dict | None = None, delete_first: bool = True) -> "frappe.Document":
	"""Create (or recreate) a test Item with minimum required fields."""
	if delete_first and frappe.db.exists("Item", item_code):
		frappe.delete_doc("Item", item_code, ignore_permissions=True, force=True)
		frappe.db.commit()

	sc = _ensure_test_sub_category()
	data = {
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_code,
		"item_group": "All Item Groups",
		"stock_uom": "Nos",
		"gst_hsn_code": TEST_HSN,
		"ch_sub_category": sc,
		"ch_lifecycle_status": "Draft",
		"ch_approval_status": "Draft",
		"ch_plm_status": "NPI",
	}
	if extra:
		data.update(extra)
	doc = frappe.get_doc(data)
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	frappe.db.commit()
	return doc


def _set_user_roles(roles: list[str]):
	"""Override frappe.get_roles for the current test."""
	frappe.local.user_roles = roles


class _MockDoc:
	"""Minimal doc mock where .items is a list (not the dict built-in method)."""
	def __init__(self, doctype: str, items: list):
		self.doctype = doctype
		self.items = items


class _MockRow:
	"""Minimal row mock."""
	def __init__(self, idx: int, item_code: str):
		self.idx = idx
		self.item_code = item_code


# ─────────────────────────────────────────────────────────────────────────────
# 1. Item Revision / Version Control
# ─────────────────────────────────────────────────────────────────────────────

class TestTierCVersionControl(unittest.TestCase):

	def setUp(self):
		frappe.set_user("Administrator")

	def test_01_version_created_on_insert(self):
		"""Saving a new item creates a CH Item Version with version_number=1."""
		item = _make_item("TC-VER-01")
		versions = frappe.get_all(
			"CH Item Version",
			filters={"item_code": item.name},
			fields=["version_number"],
			order_by="version_number asc",
		)
		self.assertTrue(len(versions) >= 1, "Expected at least one version after insert")
		self.assertEqual(versions[0].version_number, 1)

	def test_02_version_increments_on_save(self):
		"""Each subsequent save increments the version number."""
		item = _make_item("TC-VER-02")
		item.reload()
		v_before = frappe.db.count("CH Item Version", {"item_code": item.name})
		item.description = "Updated description"
		item.flags.ignore_mandatory = True
		item.save(ignore_permissions=True)
		frappe.db.commit()
		v_after = frappe.db.count("CH Item Version", {"item_code": item.name})
		self.assertEqual(v_after, v_before + 1)

	def test_03_get_item_versions_api(self):
		"""get_item_versions() returns list ordered newest-first."""
		item = _make_item("TC-VER-03")
		item.reload()
		item.description = "v2 change"
		item.flags.ignore_mandatory = True
		item.save(ignore_permissions=True)
		frappe.db.commit()

		from ch_item_master.ch_item_master.tier_c import get_item_versions
		versions = get_item_versions(item.name)
		self.assertGreaterEqual(len(versions), 2)
		nums = [v["version_number"] for v in versions]
		self.assertEqual(nums, sorted(nums, reverse=True))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Formal Model Approval Gate
# ─────────────────────────────────────────────────────────────────────────────

class TestTierCApprovalGate(unittest.TestCase):

	def setUp(self):
		frappe.set_user("Administrator")

	def test_04_cannot_activate_without_approval(self):
		"""Setting lifecycle = Active without ch_approval_status = Approved must throw."""
		from ch_item_master.ch_item_master.tier_c import ApprovalError, enforce_approval_gate

		item = _make_item("TC-APR-04")
		item.ch_lifecycle_status = "Active"
		item.ch_approval_status = "Draft"

		# Temporarily remove bypass roles from session
		original_roles = frappe.get_roles
		frappe.get_roles = lambda u=None: ["Stock User"]
		try:
			with self.assertRaises(frappe.ValidationError):
				enforce_approval_gate(item)
		finally:
			frappe.get_roles = original_roles

	def test_05_submit_approve_flow(self):
		"""Full approve flow: Draft → Submitted for Review → Approved."""
		from ch_item_master.ch_item_master.tier_c import submit_for_approval, approve_item

		item = _make_item("TC-APR-05")
		result = submit_for_approval(item.name, remarks="Ready for review")
		self.assertEqual(result, "Submitted for Review")

		status = frappe.db.get_value("Item", item.name, "ch_approval_status")
		self.assertEqual(status, "Submitted for Review")

		result2 = approve_item(item.name, remarks="Looks good")
		self.assertEqual(result2, "Approved")

		final = frappe.db.get_value("Item", item.name, "ch_approval_status")
		self.assertEqual(final, "Approved")

	def test_06_reject_flow(self):
		"""Approver can reject a submitted item; approval_status = Rejected."""
		from ch_item_master.ch_item_master.tier_c import submit_for_approval, reject_item

		item = _make_item("TC-APR-06")
		submit_for_approval(item.name)
		result = reject_item(item.name, remarks="Does not meet spec")
		self.assertEqual(result, "Rejected")
		self.assertEqual(
			frappe.db.get_value("Item", item.name, "ch_approval_status"),
			"Rejected",
		)


# ─────────────────────────────────────────────────────────────────────────────
# 3. GTIN / EAN / UPC + Trading Partner Aliases
# ─────────────────────────────────────────────────────────────────────────────

class TestTierCGTIN(unittest.TestCase):

	def setUp(self):
		frappe.set_user("Administrator")

	def test_07_valid_ean13_accepted(self):
		"""A correctly check-digit-ed EAN-13 GTIN is accepted on save."""
		# 5901234123457 is a valid EAN-13 (check digit = 7)
		item = _make_item("TC-GTIN-07", extra={"ch_gtin": "5901234123457"})
		self.assertEqual(
			frappe.db.get_value("Item", item.name, "ch_gtin"),
			"5901234123457",
		)

	def test_08_invalid_gtin_rejected(self):
		"""A GTIN with wrong check digit throws GTINError on save."""
		from ch_item_master.ch_item_master.tier_c import GTINError, validate_gtin

		item_stub = frappe._dict(ch_gtin="5901234123458")  # wrong check digit
		with self.assertRaises(frappe.ValidationError):
			validate_gtin(item_stub)

	def test_09_trading_partner_aliases_stored(self):
		"""Trading partner aliases saved on Item are retrievable via API."""
		from ch_item_master.ch_item_master.tier_c import get_trading_partner_aliases

		# Ensure a supplier exists
		if not frappe.db.exists("Supplier", "Test Supplier TierC"):
			sup = frappe.get_doc({
				"doctype": "Supplier",
				"supplier_name": "Test Supplier TierC",
				"supplier_group": frappe.db.get_value("Supplier Group", {}, "name") or "All Supplier Groups",
			})
			sup.flags.ignore_mandatory = True
			sup.insert(ignore_permissions=True, ignore_mandatory=True)

		item = _make_item("TC-GTIN-09", extra={
			"ch_trading_partner_aliases": [{
				"partner_type": "Supplier",
				"partner": "Test Supplier TierC",
				"partner_item_code": "SUP-001",
				"partner_item_name": "Supplier Laptop",
				"is_primary": 1,
			}],
		})
		frappe.db.commit()

		aliases = get_trading_partner_aliases(item.name)
		self.assertTrue(any(a.partner_item_code == "SUP-001" for a in aliases))


# ─────────────────────────────────────────────────────────────────────────────
# 4. MRP / Coverage Planning + Site-Level Defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestTierCMRP(unittest.TestCase):

	def setUp(self):
		frappe.set_user("Administrator")

	def test_10_mrp_fields_saved(self):
		"""MRP planning fields are stored and retrievable on Item."""
		item = _make_item("TC-MRP-10", extra={
			"ch_mrp_type": "Reorder Point",
			"ch_reorder_point": 50.0,
			"ch_safety_stock_days": 7,
			"ch_procurement_lead_days": 14,
			"ch_lot_size": 100.0,
		})
		reloaded = frappe.get_doc("Item", item.name)
		self.assertEqual(reloaded.ch_mrp_type, "Reorder Point")
		self.assertEqual(reloaded.ch_reorder_point, 50.0)
		self.assertEqual(reloaded.ch_safety_stock_days, 7)
		self.assertEqual(reloaded.ch_procurement_lead_days, 14)
		self.assertEqual(reloaded.ch_lot_size, 100.0)

	def test_11_site_defaults_stored(self):
		"""Site-level defaults child rows are stored on Item."""
		from ch_item_master.ch_item_master.tier_c import get_site_defaults

		# Find a real warehouse
		wh = frappe.db.get_value("Warehouse", {"is_group": 0}, "name")
		if not wh:
			self.skipTest("No warehouse found in test DB")

		item = _make_item("TC-MRP-11", extra={
			"ch_site_defaults": [{
				"warehouse": wh,
				"safety_stock": 10.0,
				"reorder_point": 25.0,
				"lead_time_days": 5,
				"min_order_qty": 20.0,
			}],
		})
		frappe.db.commit()

		rows = get_site_defaults(item.name)
		self.assertTrue(len(rows) >= 1)
		row = get_site_defaults(item.name, warehouse=wh)
		self.assertIsNotNone(row)
		self.assertEqual(row.reorder_point, 25.0)

	def test_12_site_defaults_unknown_warehouse_returns_none(self):
		"""get_site_defaults for unknown warehouse returns None."""
		from ch_item_master.ch_item_master.tier_c import get_site_defaults
		item = _make_item("TC-MRP-12")
		result = get_site_defaults(item.name, warehouse="NonExistent Warehouse - XX")
		self.assertIsNone(result)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Vendor Info-Record Sourcing Master
# ─────────────────────────────────────────────────────────────────────────────

class TestTierCVendorInfoRecord(unittest.TestCase):

	def setUp(self):
		frappe.set_user("Administrator")
		# Ensure supplier exists
		if not frappe.db.exists("Supplier", "VIR Test Supplier"):
			sg = frappe.db.get_value("Supplier Group", {}, "name") or "All Supplier Groups"
			sup = frappe.get_doc({
				"doctype": "Supplier",
				"supplier_name": "VIR Test Supplier",
				"supplier_group": sg,
			})
			sup.flags.ignore_mandatory = True
			sup.insert(ignore_permissions=True, ignore_mandatory=True)

	def test_13_create_vendor_info_record(self):
		"""upsert_vendor_info creates a CH Vendor Info Record."""
		from ch_item_master.ch_item_master.tier_c import upsert_vendor_info, get_vendor_info

		item = _make_item("TC-VIR-13")

		# Clean up any leftover
		existing = frappe.db.get_value(
			"CH Vendor Info Record",
			{"item_code": item.name, "supplier": "VIR Test Supplier"},
			"name",
		)
		if existing:
			frappe.delete_doc("CH Vendor Info Record", existing, ignore_permissions=True)

		name = upsert_vendor_info(
			item.name,
			"VIR Test Supplier",
			vendor_item_code="VND-TC-001",
			standard_price=4500.0,
			lead_time_days=10,
			preferred=1,
		)
		self.assertTrue(name)

		info = get_vendor_info(item.name, supplier="VIR Test Supplier")
		self.assertIsNotNone(info)
		self.assertEqual(info.vendor_item_code, "VND-TC-001")
		self.assertEqual(info.standard_price, 4500.0)

	def test_14_upsert_updates_existing_record(self):
		"""upsert_vendor_info updates an existing record without creating a duplicate."""
		from ch_item_master.ch_item_master.tier_c import upsert_vendor_info, get_vendor_info

		item = _make_item("TC-VIR-14")

		# Clean up
		for row in frappe.get_all("CH Vendor Info Record", {"item_code": item.name}):
			frappe.delete_doc("CH Vendor Info Record", row.name, ignore_permissions=True)

		upsert_vendor_info(item.name, "VIR Test Supplier", standard_price=1000.0)
		upsert_vendor_info(item.name, "VIR Test Supplier", standard_price=1200.0)

		count = frappe.db.count(
			"CH Vendor Info Record",
			{"item_code": item.name, "supplier": "VIR Test Supplier"},
		)
		self.assertEqual(count, 1)
		info = get_vendor_info(item.name, supplier="VIR Test Supplier")
		self.assertEqual(info.standard_price, 1200.0)

	def test_15_get_vendor_info_returns_preferred_first(self):
		"""get_vendor_info without supplier filter returns preferred records first."""
		from ch_item_master.ch_item_master.tier_c import upsert_vendor_info, get_vendor_info

		item = _make_item("TC-VIR-15")

		# Ensure second supplier
		if not frappe.db.exists("Supplier", "VIR Test Supplier 2"):
			sg = frappe.db.get_value("Supplier Group", {}, "name") or "All Supplier Groups"
			frappe.get_doc({
				"doctype": "Supplier",
				"supplier_name": "VIR Test Supplier 2",
				"supplier_group": sg,
			}).insert(ignore_permissions=True, ignore_mandatory=True)

		# Clean
		for row in frappe.get_all("CH Vendor Info Record", {"item_code": item.name}):
			frappe.delete_doc("CH Vendor Info Record", row.name, ignore_permissions=True)

		upsert_vendor_info(item.name, "VIR Test Supplier", standard_price=800.0, preferred=0)
		upsert_vendor_info(item.name, "VIR Test Supplier 2", standard_price=750.0, preferred=1)

		all_info = get_vendor_info(item.name)
		self.assertGreaterEqual(len(all_info), 2)
		self.assertEqual(all_info[0].preferred, 1)

	def test_16_effective_vendor_source_respects_moq(self):
		"""When qty is below MOQ, source resolver skips that vendor."""
		from ch_item_master.ch_item_master.tier_c import upsert_vendor_info, get_effective_vendor_source

		item = _make_item("TC-VIR-16")

		# Ensure second supplier
		if not frappe.db.exists("Supplier", "VIR Test Supplier 3"):
			sg = frappe.db.get_value("Supplier Group", {}, "name") or "All Supplier Groups"
			frappe.get_doc({
				"doctype": "Supplier",
				"supplier_name": "VIR Test Supplier 3",
				"supplier_group": sg,
			}).insert(ignore_permissions=True, ignore_mandatory=True)

		for row in frappe.get_all("CH Vendor Info Record", {"item_code": item.name}):
			frappe.delete_doc("CH Vendor Info Record", row.name, ignore_permissions=True)

		upsert_vendor_info(item.name, "VIR Test Supplier", standard_price=900.0, min_order_qty=10, preferred=1)
		upsert_vendor_info(item.name, "VIR Test Supplier 3", standard_price=950.0, min_order_qty=1, preferred=0)

		chosen = get_effective_vendor_source(item.name, qty=5)
		self.assertIsNotNone(chosen)
		self.assertEqual(chosen.get("supplier"), "VIR Test Supplier 3")

	def test_17_effective_vendor_source_uses_price_break(self):
		"""Quantity break price should override standard vendor price when matched."""
		from ch_item_master.ch_item_master.tier_c import upsert_vendor_info, get_effective_vendor_source

		item = _make_item("TC-VIR-17")

		for row in frappe.get_all("CH Vendor Info Record", {"item_code": item.name}):
			frappe.delete_doc("CH Vendor Info Record", row.name, ignore_permissions=True)

		name = upsert_vendor_info(item.name, "VIR Test Supplier", standard_price=1000.0, min_order_qty=1, preferred=1)
		rec = frappe.get_doc("CH Vendor Info Record", name)
		rec.append(
			"price_breaks",
			{
				"min_qty": 10,
				"max_qty": 9999,
				"unit_price": 840.0,
				"is_active": 1,
			},
		)
		rec.save(ignore_permissions=True)

		chosen = get_effective_vendor_source(item.name, qty=20)
		self.assertIsNotNone(chosen)
		self.assertEqual(chosen.get("supplier"), "VIR Test Supplier")
		self.assertEqual(float(chosen.get("effective_unit_price")), 840.0)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Full PLM State Machine
# ─────────────────────────────────────────────────────────────────────────────

class TestTierCPLMStateMachine(unittest.TestCase):

	def setUp(self):
		frappe.set_user("Administrator")

	def _item_with_plm(self, item_code: str, plm_status: str) -> "frappe.Document":
		"""Create an item and force-set its PLM status in DB directly (bypassing transition)."""
		item = _make_item(item_code)
		frappe.db.set_value("Item", item.name, "ch_plm_status", plm_status, update_modified=False)
		frappe.db.commit()
		return frappe.get_doc("Item", item.name)

	def test_16_valid_plm_transition_npi_to_under_review(self):
		"""NPI → Under Review is a valid PLM transition."""
		from ch_item_master.ch_item_master.tier_c import validate_plm_transition

		item = self._item_with_plm("TC-PLM-16", "NPI")
		item.ch_plm_status = "Under Review"
		# Should not raise
		validate_plm_transition(item)

	def test_17_invalid_plm_transition_raises(self):
		"""NPI → Active Production is an invalid PLM transition and must raise."""
		from ch_item_master.ch_item_master.tier_c import validate_plm_transition, PLMError

		item = self._item_with_plm("TC-PLM-17", "NPI")
		item.ch_plm_status = "Active Production"
		with self.assertRaises(frappe.ValidationError):
			validate_plm_transition(item)

	def test_18_discontinued_is_terminal(self):
		"""Discontinued → any other state raises (terminal state)."""
		from ch_item_master.ch_item_master.tier_c import validate_plm_transition

		item = self._item_with_plm("TC-PLM-18", "Discontinued")
		item.ch_plm_status = "NPI"
		with self.assertRaises(frappe.ValidationError):
			validate_plm_transition(item)

	def test_19_discontinued_blocks_purchase_order(self):
		"""Sales Invoice validate must block items with PLM = Discontinued."""
		from ch_item_master.ch_item_master.tier_c import enforce_plm_on_transaction, PLMError

		item = self._item_with_plm("TC-PLM-19", "Discontinued")

		# Simulate a Purchase Order doc
		po = _MockDoc(
			doctype="Purchase Order",
			items=[_MockRow(idx=1, item_code=item.name)],
		)
		with self.assertRaises(frappe.ValidationError):
			enforce_plm_on_transaction(po)

	def test_20_end_of_life_warns_on_sales(self):
		"""Sales Invoice validate emits a msgprint warning for End of Life items (no hard block)."""
		from ch_item_master.ch_item_master.tier_c import enforce_plm_on_transaction

		item = self._item_with_plm("TC-PLM-20", "End of Life")

		si = _MockDoc(
			doctype="Sales Invoice",
			items=[_MockRow(idx=1, item_code=item.name)],
		)
		# Should not raise (only msgprint)
		try:
			enforce_plm_on_transaction(si)
		except frappe.ValidationError:
			self.fail("End of Life should warn, not block, on Sales Invoice")


if __name__ == "__main__":
	unittest.main()
