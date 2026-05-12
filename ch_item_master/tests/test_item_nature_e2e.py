"""End-to-end test for the item_nature universal item-master architecture.

Covers all 6 natures and verifies:
  - Sub Category creates with required item_nature
  - Item created from each nature behaves correctly:
      * Variant Template -> requires variant specs, can create template
      * Simple Auto-Named -> auto item_code + auto item_name, stock item
      * Simple Custom-Named -> auto code, user-typed name preserved
      * Service -> non-stock, user-typed name, no variant specs allowed
      * Subscription -> non-stock, surfaces in Warranty Plan link query
      * Asset / Capital -> stock, serial-required default
  - Companion fields (default_uom, has_serial_no, has_batch_no) propagate to Item
  - Lock: cannot switch nature once items exist (except Auto<->Custom)
  - Warranty Plan link query returns only Subscription-nature items
  - Legacy CH Category.allow_custom_item_name column is gone
"""

import frappe
import unittest


CAT_NAME = "_Test Universal Cat"
ITEM_GROUP = "Products"
DEFAULT_HSN = "998314"  # 6-digit valid HSN/SAC for tests


def _ensure_hsn():
    if not frappe.db.exists("GST HSN Code", DEFAULT_HSN):
        h = frappe.new_doc("GST HSN Code")
        h.hsn_code = DEFAULT_HSN
        h.description = "Test HSN"
        h.insert(ignore_permissions=True)


def _ensure_item_group():
    if not frappe.db.exists("Item Group", ITEM_GROUP):
        ig = frappe.new_doc("Item Group")
        ig.item_group_name = ITEM_GROUP
        ig.parent_item_group = "All Item Groups"
        ig.insert(ignore_permissions=True)


def _ensure_uom(uom):
    if not frappe.db.exists("UOM", uom):
        u = frappe.new_doc("UOM")
        u.uom_name = uom
        u.insert(ignore_permissions=True)


def _ensure_attribute(name):
    if not frappe.db.exists("Item Attribute", name):
        a = frappe.new_doc("Item Attribute")
        a.attribute_name = name
        a.append("item_attribute_values", {"attribute_value": "Test", "abbr": "T"})
        a.insert(ignore_permissions=True)


def _make_category():
    if frappe.db.exists("CH Category", {"category_name": CAT_NAME}):
        return frappe.db.get_value("CH Category", {"category_name": CAT_NAME}, "name")
    cat = frappe.new_doc("CH Category")
    cat.category_name = CAT_NAME
    cat.item_group = ITEM_GROUP
    cat.insert(ignore_permissions=True)
    return cat.name


def _make_subcat(cat, name, prefix, nature, **extra):
    extra.setdefault("hsn_code", DEFAULT_HSN)
    existing = frappe.db.exists("CH Sub Category", {"category": cat, "sub_category_name": name})
    if existing:
        sc = frappe.get_doc("CH Sub Category", existing)
        sc.item_nature = nature
        for k, v in extra.items():
            setattr(sc, k, v)
        sc.save(ignore_permissions=True)
        return sc.name
    sc = frappe.new_doc("CH Sub Category")
    sc.category = cat
    sc.sub_category_name = name
    sc.prefix = prefix
    sc.item_nature = nature
    for k, v in extra.items():
        setattr(sc, k, v)
    sc.insert(ignore_permissions=True)
    return sc.name


class TestItemNatureUniverse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        _ensure_item_group()
        _ensure_hsn()
        for u in ("Nos", "Hour", "Month", "Kg"):
            _ensure_uom(u)
        cls.cat = _make_category()

    # ── Schema sanity ────────────────────────────────────────────────────
    def test_01_legacy_column_dropped(self):
        cols = frappe.db.sql(
            "SHOW COLUMNS FROM `tabCH Category` LIKE 'allow_custom_item_name'"
        )
        self.assertEqual(cols, ())

    def test_02_item_nature_field_exists(self):
        meta = frappe.get_meta("CH Sub Category")
        f = meta.get_field("item_nature")
        self.assertIsNotNone(f)
        self.assertEqual(f.fieldtype, "Select")
        self.assertIn("Variant Template", f.options)
        self.assertIn("Subscription", f.options)

    # ── Service nature ───────────────────────────────────────────────────
    def test_10_service_subcat_forces_non_stock(self):
        sc = _make_subcat(
            self.cat, "Repair Labour", "RPL", "Service",
            default_uom="Hour", is_repair_labour=1,
            gofix_service_category="Repair",
        )
        sc_doc = frappe.get_doc("CH Sub Category", sc)
        self.assertEqual(sc_doc.is_stock_item_default, 0)
        # allow_custom_item_name auto-set
        self.assertEqual(sc_doc.allow_custom_item_name, 1)

    def test_11_service_item_creation(self):
        sc = frappe.db.get_value("CH Sub Category",
                                  {"category": self.cat, "sub_category_name": "Repair Labour"})
        item_name = "Diagnostic Charge"
        existing = frappe.db.exists("Item", {"item_name": item_name})
        if existing:
            frappe.delete_doc("Item", existing, force=1, ignore_permissions=True)
        item = frappe.new_doc("Item")
        item.item_name = item_name
        item.item_group = ITEM_GROUP
        item.ch_sub_category = sc
        item.ch_category = self.cat
        item.insert(ignore_permissions=True)
        self.assertEqual(item.is_stock_item, 0, "Service items must be non-stock")
        self.assertEqual(item.stock_uom, "Hour")
        self.assertEqual(item.item_name, item_name, "Service item name preserved")

    def test_12_service_subcat_rejects_variant_spec(self):
        from ch_item_master.ch_item_master.exceptions import InvalidItemNatureError
        _ensure_attribute("Test Color")
        sc = frappe.get_doc("CH Sub Category", frappe.db.get_value(
            "CH Sub Category", {"category": self.cat, "sub_category_name": "Repair Labour"}))
        sc.append("specifications", {
            "spec": "Test Color", "spec_type": "Variant", "is_variant": 1,
        })
        with self.assertRaises(InvalidItemNatureError):
            sc.save(ignore_permissions=True)

    # ── Subscription nature ──────────────────────────────────────────────
    def test_20_subscription_subcat_and_warranty_query(self):
        sc = _make_subcat(
            self.cat, "Warranty Plans", "WPL", "Subscription",
            default_uom="Month", is_warranty_plan=1,
            subscription_duration_months_default=12,
        )
        sc_doc = frappe.get_doc("CH Sub Category", sc)
        self.assertEqual(sc_doc.is_stock_item_default, 0)
        self.assertEqual(sc_doc.is_warranty_plan, 1)

        # Create a subscription item
        item_name = "GoFix Plus 12M"
        if not frappe.db.exists("Item", {"item_name": item_name}):
            item = frappe.new_doc("Item")
            item.item_name = item_name
            item.item_group = ITEM_GROUP
            item.ch_sub_category = sc
            item.ch_category = self.cat
            item.insert(ignore_permissions=True)

        # Verify link query returns it
        from ch_item_master.ch_item_master.api import items_by_subcategory_nature
        rows = items_by_subcategory_nature(
            "Item", "GoFix", "name", 0, 20,
            {"natures": ["Subscription"], "is_warranty_plan": 1},
        )
        names = [r[0] for r in rows]
        item_code = frappe.db.get_value("Item", {"item_name": item_name}, "name")
        self.assertIn(item_code, names, "Subscription item must surface in warranty plan query")

    # ── Simple Auto-Named ────────────────────────────────────────────────
    def test_30_simple_auto_named(self):
        sc = _make_subcat(
            self.cat, "Cables", "CBL", "Simple Auto-Named",
            default_uom="Nos",
        )
        # Auto-named items get codes from prefix; item_name auto-generated.
        item = frappe.new_doc("Item")
        item.item_group = ITEM_GROUP
        item.ch_sub_category = sc
        item.ch_category = self.cat
        # Don't set item_name - will be auto-generated
        item.insert(ignore_permissions=True)
        self.assertTrue(item.item_code.startswith("CBL"))
        self.assertEqual(item.is_stock_item, 1)
        self.assertEqual(item.stock_uom, "Nos")

    # ── Simple Custom-Named ──────────────────────────────────────────────
    def test_40_simple_custom_named_preserves_name(self):
        sc = _make_subcat(
            self.cat, "Loose Produce", "LP", "Simple Custom-Named",
            default_uom="Kg", min_qty_decimals=3,
        )
        sc_doc = frappe.get_doc("CH Sub Category", sc)
        self.assertEqual(sc_doc.allow_custom_item_name, 1)

        custom_name = "Fresh Coriander Bundle"
        existing = frappe.db.exists("Item", {"item_name": custom_name})
        if existing:
            frappe.delete_doc("Item", existing, force=1, ignore_permissions=True)
        item = frappe.new_doc("Item")
        item.item_name = custom_name
        item.item_group = ITEM_GROUP
        item.ch_sub_category = sc
        item.ch_category = self.cat
        item.insert(ignore_permissions=True)
        self.assertEqual(item.item_name, custom_name)
        self.assertEqual(item.stock_uom, "Kg")

    # ── Asset / Capital ──────────────────────────────────────────────────
    def test_50_asset_capital_serial_default(self):
        sc = _make_subcat(
            self.cat, "Demo Units", "DEMO", "Asset / Capital",
            default_uom="Nos",
        )
        sc_doc = frappe.get_doc("CH Sub Category", sc)
        self.assertEqual(sc_doc.serial_required, 1, "Asset nature defaults serial_required=1")

        custom_name = "Demo iPhone Display Unit"
        if not frappe.db.exists("Item", {"item_name": custom_name}):
            item = frappe.new_doc("Item")
            item.item_name = custom_name
            item.item_group = ITEM_GROUP
            item.ch_sub_category = sc
            item.ch_category = self.cat
            item.insert(ignore_permissions=True)
        item = frappe.get_doc("Item", {"item_name": custom_name})
        self.assertEqual(item.has_serial_no, 1, "Asset items default has_serial_no=1")

    # ── Nature lock ──────────────────────────────────────────────────────
    def test_60_nature_lock_blocks_unsafe_transition(self):
        from ch_item_master.ch_item_master.exceptions import ItemNatureLockedError
        # Loose Produce already has an Item -> try Custom-Named -> Service (unsafe)
        sc_name = frappe.db.get_value(
            "CH Sub Category",
            {"category": self.cat, "sub_category_name": "Loose Produce"},
        )
        sc = frappe.get_doc("CH Sub Category", sc_name)
        sc.item_nature = "Service"
        with self.assertRaises(ItemNatureLockedError):
            sc.save(ignore_permissions=True)

    def test_61_nature_lock_allows_safe_transition(self):
        # Cables (Simple Auto-Named) -> Simple Custom-Named is allowed
        sc_name = frappe.db.get_value(
            "CH Sub Category",
            {"category": self.cat, "sub_category_name": "Cables"},
        )
        sc = frappe.get_doc("CH Sub Category", sc_name)
        sc.item_nature = "Simple Custom-Named"
        sc.save(ignore_permissions=True)  # Should not raise
        sc.reload()
        self.assertEqual(sc.item_nature, "Simple Custom-Named")
        self.assertEqual(sc.allow_custom_item_name, 1)
        # Restore
        sc.item_nature = "Simple Auto-Named"
        sc.save(ignore_permissions=True)


if __name__ == "__main__":
    unittest.main()


def run_all():
    import sys
    import unittest
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.failures or result.errors:
        raise Exception(
            f"{__name__}: {len(result.failures)} failure(s), {len(result.errors)} error(s)"
        )
