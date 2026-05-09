"""End-to-end test for Tier B SAP-parity features.

Coverage:
  1. CH Item Substitute child table can be saved on an Item
  2. get_active_substitutes returns only date-valid rows
  3. get_completeness_score returns 0-100 with grade
  4. MSP enforcement hard-blocks non-approver below MSP
  5. MSP enforcement warns (not blocks) approver below MSP
  6. MSP enforcement passes when rate >= MSP
  7. enforce_expiry blocks expired batch on Delivery Note
  8. enforce_expiry warns near-expiry batch
  9. enforce_expiry passes valid batch
  10. track_standard_cost writes audit row on change
  11. Country HS codes table persists to DB
  12. UOM defaults applied from Sub Category on item insert
  13. parent_category field exists on CH Category
  14. CH UOM Conversion Rule doctype exists
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import today, add_days

ITEM_GROUP = "Products"
TEST_HSN = "01011010"  # Standard GST HSN Code that exists in DB (8 digits)
_TEST_CAT = "_TierB_Test_Category"
_TEST_SC = None  # Filled by _ensure_test_sub_category()
_TEST_MODEL = "_TierB_Test_Model"


def _ensure_item_group():
    if not frappe.db.exists("Item Group", ITEM_GROUP):
        ig = frappe.new_doc("Item Group")
        ig.item_group_name = ITEM_GROUP
        ig.parent_item_group = "All Item Groups"
        ig.insert(ignore_permissions=True)



def _ensure_test_sub_category() -> str:
    """Ensure a shared test CH Category + CH Sub Category exist and return Sub Category name."""
    global _TEST_SC
    if _TEST_SC:
        return _TEST_SC
    _ensure_item_group()
    if not frappe.db.exists("CH Category", _TEST_CAT):
        cat = frappe.new_doc("CH Category")
        cat.category_name = _TEST_CAT
        cat.item_group = ITEM_GROUP
        cat.lifecycle_status = "Active"
        cat.insert(ignore_permissions=True)
    sc_name = frappe.db.get_value("CH Sub Category", {"category": _TEST_CAT}, "name")
    if not sc_name:
        sc = frappe.new_doc("CH Sub Category")
        sc.sub_category_name = "_TierB_SC"
        sc.category = _TEST_CAT
        sc.prefix = "TB01"
        sc.item_nature = "Simple Auto-Named"
        sc.lifecycle_status = "Active"
        sc.insert(ignore_permissions=True)
        sc_name = sc.name
    _TEST_SC = sc_name
    return sc_name


def _make_item(**kwargs) -> str:
    """Create a stock item with ch_lifecycle_status=Active and return its name."""
    _ensure_item_group()
    sc = _ensure_test_sub_category()
    doc = frappe.new_doc("Item")
    item_name = kwargs.get("item_name", frappe.generate_hash(length=8))
    # Delete existing item to allow clean re-creation
    if frappe.db.exists("Item", item_name):
        frappe.delete_doc("Item", item_name, force=1, ignore_permissions=True)
    doc.item_name = item_name
    doc.item_code = item_name  # Frappe requires item_code to be set explicitly
    doc.item_group = ITEM_GROUP
    doc.stock_uom = kwargs.get("stock_uom", "Nos")
    doc.is_stock_item = 1
    doc.gst_hsn_code = TEST_HSN
    doc.ch_sub_category = sc
    doc.ch_category = _TEST_CAT
    for k, v in kwargs.items():
        if k not in ("item_name", "stock_uom"):
            setattr(doc, k, v)
    doc.ch_lifecycle_status = "Active"
    frappe.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True, ignore_mandatory=True)
    frappe.flags.ignore_mandatory = False
    return doc.name


def _make_uom(name: str):
    if not frappe.db.exists("UOM", name):
        u = frappe.new_doc("UOM")
        u.uom_name = name
        u.insert(ignore_permissions=True)


class TestTierBSubstitutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()
        cls.item_a = _make_item(item_name="_TierB_Item_A")
        cls.item_b = _make_item(item_name="_TierB_Item_B")

    def test_01_substitute_row_saved(self):
        """CH Item Substitute child table row can be added and persisted."""
        doc = frappe.get_doc("Item", self.item_a)
        doc.append("ch_item_substitutes", {
            "substitute_item": self.item_b,
            "substitute_type": "Alternate",
            "priority": 1,
        })
        doc.flags.ignore_mandatory = True
        doc.save(ignore_permissions=True)
        reloaded = frappe.get_doc("Item", self.item_a)
        rows = [r for r in reloaded.get("ch_item_substitutes", []) if r.substitute_item == self.item_b]
        self.assertEqual(len(rows), 1, "Substitute row should be persisted")

    def test_02_get_active_substitutes_date_filter(self):
        """get_active_substitutes returns only date-valid rows."""
        from ch_item_master.ch_item_master.tier_b import get_active_substitutes

        # Ensure at least one row exists (from test_01)
        result = get_active_substitutes(self.item_a)
        if isinstance(result, str):
            import json
            result = json.loads(result)
        # All returned rows must be date-valid
        today_str = today()
        for row in result:
            if row.get("effective_from"):
                self.assertLessEqual(row["effective_from"], today_str)
            if row.get("effective_to"):
                self.assertGreaterEqual(row["effective_to"], today_str)


class TestTierBCompletenessScore(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()
        cls.item = _make_item(item_name="_TierB_Score_Item")

    def test_03_completeness_score_returns_grade(self):
        """get_completeness_score returns dict with score 0-100 and grade A/B/C/D."""
        from ch_item_master.ch_item_master.tier_b import get_completeness_score

        result = get_completeness_score(self.item)
        if isinstance(result, str):
            import json
            result = json.loads(result)
        self.assertIn("score", result, "Result must have 'score'")
        self.assertIn("grade", result, "Result must have 'grade'")
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)
        self.assertIn(result["grade"], ("A", "B", "C", "D"))


class TestTierBMSP(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()
        cls.item = _make_item(item_name="_TierB_MSP_Item", ch_minimum_selling_price=500.0)

    def _make_invoice_doc(self, rate: float):
        """Create an unsaved Sales Invoice doc stub for MSP testing."""
        doc = frappe.new_doc("Sales Invoice")
        doc.customer = "_Test Customer"
        doc.append("items", {
            "item_code": self.item,
            "qty": 1,
            "rate": rate,
            "uom": "Nos",
            "idx": 1,
        })
        return doc

    def test_04_msp_blocks_non_approver(self):
        """MSP enforce hard-blocks non-approver when rate < MSP."""
        from ch_item_master.ch_item_master.tier_b import enforce_msp
        import ch_item_master.ch_item_master.tier_b as tier_b_mod
        original_user_roles = tier_b_mod._user_roles
        try:
            # Patch _user_roles to return non-approver roles
            tier_b_mod._user_roles = lambda: {"Desk User", "All"}
            doc = self._make_invoice_doc(rate=100.0)
            with self.assertRaises(frappe.ValidationError):
                enforce_msp(doc)
        finally:
            tier_b_mod._user_roles = original_user_roles

    def test_05_msp_warns_approver(self):
        """MSP enforce warns (does not raise) for approver below MSP."""
        from ch_item_master.ch_item_master.tier_b import enforce_msp

        # Administrator has System Manager role which is in _MSP_BYPASS_ROLES
        doc = self._make_invoice_doc(rate=100.0)
        try:
            enforce_msp(doc)  # Should NOT raise
        except frappe.ValidationError:
            self.fail("MSP enforce should warn, not raise, for approvers")

    def test_06_msp_passes_when_rate_ok(self):
        """MSP enforce passes with no error when rate >= MSP."""
        from ch_item_master.ch_item_master.tier_b import enforce_msp

        doc = self._make_invoice_doc(rate=600.0)
        try:
            enforce_msp(doc)  # Must not raise
        except frappe.ValidationError:
            self.fail("MSP enforce raised unexpectedly when rate > MSP")


class TestTierBExpiryEnforcement(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()

        # Create a batch-tracked item with ch_enforce_expiry
        cls.item = _make_item(
            item_name="_TierB_Expiry_Item",
            has_batch_no=1,
            ch_enforce_expiry=1,
        )

        # Create a warehouse for the DN
        existing_wh = frappe.db.get_value("Warehouse", {"warehouse_name": "_TierB Test"}, "name")
        if not existing_wh:
            wh = frappe.new_doc("Warehouse")
            wh.warehouse_name = "_TierB Test"
            wh.company = frappe.db.get_single_value("Global Defaults", "default_company") or "_Test Company"
            wh.insert(ignore_permissions=True)
            cls.warehouse = wh.name
        else:
            cls.warehouse = existing_wh

        # Expired batch
        cls.expired_batch = frappe.generate_hash(length=8)
        b = frappe.new_doc("Batch")
        b.batch_id = cls.expired_batch
        b.item = cls.item
        b.expiry_date = add_days(today(), -10)
        b.insert(ignore_permissions=True)

        # Near-expiry batch
        cls.near_batch = frappe.generate_hash(length=8)
        b2 = frappe.new_doc("Batch")
        b2.batch_id = cls.near_batch
        b2.item = cls.item
        b2.expiry_date = add_days(today(), 3)
        b2.insert(ignore_permissions=True)

        # Valid batch
        cls.valid_batch = frappe.generate_hash(length=8)
        b3 = frappe.new_doc("Batch")
        b3.batch_id = cls.valid_batch
        b3.item = cls.item
        b3.expiry_date = add_days(today(), 60)
        b3.insert(ignore_permissions=True)

    def _make_dn(self, batch_no: str):
        doc = frappe.new_doc("Delivery Note")
        doc.customer = "_Test Customer"
        doc.append("items", {
            "item_code": self.item,
            "qty": 1,
            "uom": "Nos",
            "batch_no": batch_no,
            "idx": 1,
        })
        return doc

    def test_07_expired_batch_blocked(self):
        """enforce_expiry raises ValidationError for expired batch."""
        from ch_item_master.ch_item_master.tier_b import enforce_expiry

        doc = self._make_dn(self.expired_batch)
        with self.assertRaises(frappe.ValidationError):
            enforce_expiry(doc)

    def test_08_near_expiry_warns(self):
        """enforce_expiry warns (does not raise) for near-expiry batch."""
        from ch_item_master.ch_item_master.tier_b import enforce_expiry

        doc = self._make_dn(self.near_batch)
        try:
            enforce_expiry(doc)  # Should warn but not raise
        except frappe.ValidationError:
            self.fail("enforce_expiry should warn for near-expiry, not raise")

    def test_09_valid_batch_passes(self):
        """enforce_expiry passes silently for valid (non-expired) batch."""
        from ch_item_master.ch_item_master.tier_b import enforce_expiry

        doc = self._make_dn(self.valid_batch)
        try:
            enforce_expiry(doc)
        except frappe.ValidationError:
            self.fail("enforce_expiry should pass for valid batch")


class TestTierBStandardCost(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()
        cls.item = _make_item(item_name="_TierB_StdCost_Item")

    def test_10_standard_cost_change_writes_audit(self):
        """Changing ch_standard_cost triggers an audit log entry."""
        from ch_item_master.ch_item_master.tier_b import track_standard_cost

        item_doc = frappe.get_doc("Item", self.item)
        old_cost = 0
        new_cost = 1500.0

        # Save old cost first
        frappe.db.set_value("Item", self.item, "ch_standard_cost", old_cost)

        item_doc.ch_standard_cost = new_cost
        # Simulate on_update call
        track_standard_cost(item_doc)

        audit_count = frappe.db.count(
            "CH Item Audit Log",
            {"item": self.item, "action": "Standard Cost Changed"},
        )
        self.assertGreater(audit_count, 0, "Audit log entry should be created for standard cost change")


class TestTierBCountryHS(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()

        # Ensure India country exists
        if not frappe.db.exists("Country", "India"):
            c = frappe.new_doc("Country")
            c.country_name = "India"
            c.insert(ignore_permissions=True)

        cls.item = _make_item(item_name="_TierB_HS_Item")

    def test_11_country_hs_codes_persist(self):
        """CH Item Country HS child table rows persist to DB."""
        doc = frappe.get_doc("Item", self.item)
        doc.append("ch_country_hs_codes", {
            "country": "India",
            "hs_code": "84713000",
            "description": "Test HS code",
            "customs_duty_pct": 7.5,
        })
        doc.flags.ignore_mandatory = True
        doc.save(ignore_permissions=True)
        reloaded = frappe.get_doc("Item", self.item)
        rows = [r for r in reloaded.get("ch_country_hs_codes", []) if r.hs_code == "84713000"]
        self.assertEqual(len(rows), 1, "Country HS code row should persist")


class TestTierBUOMDefaults(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()

        # Ensure UOMs exist
        _make_uom("_TierB Pcs")
        _make_uom("_TierB Box")

        # Ensure CH Category
        if not frappe.db.exists("CH Category", "_TierB Cat"):
            cat = frappe.new_doc("CH Category")
            cat.category_name = "_TierB Cat"
            cat.item_group = ITEM_GROUP
            cat.lifecycle_status = "Active"
            cat.insert(ignore_permissions=True)

        # Ensure CH Sub Category with UOM conversions
        sc_name = "_TierB Cat-_TierB SubCat"
        if not frappe.db.exists("CH Sub Category", sc_name):
            sc = frappe.new_doc("CH Sub Category")
            sc.sub_category_name = "_TierB SubCat"
            sc.category = "_TierB Cat"
            sc.prefix = "TB02"
            sc.item_nature = "Simple Auto-Named"
            sc.lifecycle_status = "Active"
            sc.append("ch_uom_conversions", {
                "to_uom": "_TierB Box",
                "conversion_factor": 12,
            })
            sc.insert(ignore_permissions=True)
            cls.sub_category = sc.name
        else:
            cls.sub_category = sc_name

    def test_12_uom_defaults_applied_on_insert(self):
        """UOM conversions from CH Sub Category are copied to new item.uoms."""
        from ch_item_master.ch_item_master.tier_b import apply_uom_defaults

        doc = frappe.new_doc("Item")
        doc.item_name = "_TierB UOM Test " + frappe.generate_hash(length=4)
        doc.item_group = ITEM_GROUP
        doc.stock_uom = "_TierB Pcs"
        doc.ch_sub_category = self.sub_category
        doc.ch_lifecycle_status = "Active"

        apply_uom_defaults(doc)

        uoms = {row.uom for row in (doc.uoms or [])}
        self.assertIn("_TierB Box", uoms, "Box UOM should be applied from Sub Category defaults")


class TestTierBCategoryHierarchy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.clear_cache()
        _ensure_item_group()

    def test_13_parent_category_field_exists(self):
        """parent_category field exists on CH Category doctype."""
        meta = frappe.get_meta("CH Category")
        field = meta.get_field("parent_category")
        self.assertIsNotNone(field, "parent_category field should exist on CH Category")
        self.assertEqual(field.fieldtype, "Link")
        self.assertEqual(field.options, "CH Category")

    def test_14_uom_conversion_rule_doctype_exists(self):
        """CH UOM Conversion Rule doctype is registered."""
        exists = frappe.db.exists("DocType", "CH UOM Conversion Rule")
        self.assertTrue(exists, "CH UOM Conversion Rule doctype should be installed")
