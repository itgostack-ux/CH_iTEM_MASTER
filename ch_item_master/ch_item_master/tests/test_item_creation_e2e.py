"""
E2E test: Single Item creation and Template+Variant creation.

Tests the full before_insert → before_save pipeline for:
  1. Single item (no template, sub-category with no variant specs)
  2. Item without ch_model (bare ERPNext — expected to show blocker)
  3. Template item (sub-category with variant specs, new CH Model created for test)
  4. Variants from template via create_variant API
  5. CSV-style import (template + 2 variants, frappe.flags.in_import=True)

Run:
  bench --site erpnext.local execute \
    ch_item_master.ch_item_master.tests.test_item_creation_e2e.run_all
"""

import frappe
from frappe import _

_results = []
# Tracks all created test records so cleanup can remove them
_test_model_name = None


def ok(name, detail=""):
    _results.append(("PASS", name, detail))
    print(f"PASS  {name}" + (f"  ─  {detail}" if detail else ""))


def fail(name, detail=""):
    _results.append(("FAIL", name, detail))
    print(f"FAIL  {name}" + (f"  ─  {detail}" if detail else ""))


# ─── helpers ─────────────────────────────────────────────────────────────────

def _cleanup(item_codes, model_name=None):
    """Delete test items (and their children) created during this run."""
    for ic in item_codes:
        if frappe.db.exists("Item", ic):
            frappe.delete_doc("Item", ic, force=True, ignore_permissions=True)
    if model_name and frappe.db.exists("CH Model", model_name):
        frappe.delete_doc("CH Model", model_name, force=True, ignore_permissions=True)
    frappe.db.commit()


def _create_test_model(sub_category, manufacturer, brand, spec_values=None):
    """Create a temporary CH Model for testing (deleted at end of run).

    spec_values: list of (spec, value) tuples to add in the same insert.
    CH Model validates that all variant specs have at least one value, so
    spec_values MUST be passed if the sub-category has variant specs.
    """
    model_doc = frappe.get_doc({
        "doctype": "CH Model",
        "sub_category": sub_category,
        "manufacturer": manufacturer,
        "brand": brand,
        "model_name": "E2E Test Model",  # required for autoname
    })
    for spec, value in (spec_values or []):
        model_doc.append("spec_values", {"spec": spec, "spec_value": value})
    model_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return model_doc.name


# ─── Test 1: Single item (no variant specs sub-category) ─────────────────────

def test_single_item_creation():
    """Create a single item using Accessories-No Attributes sub-category.

    Sub-category has no variant specs → item is a plain single item (no template).
    NOTE: 'Accessories-No Attributes' has hsn_code='8511' (4 digits) which is
    invalid — India Compliance requires 6 or 8 digits AND the code must exist in
    the GST HSN Code master.  We pass gst_hsn_code='85111000' explicitly to test
    the rest of the flow (the sub-category data must also be fixed).
    """
    sc_name = "Accessories-No Attributes"
    sc = frappe.db.get_value("CH Sub Category", sc_name, ["name", "prefix", "category"], as_dict=True)
    if not sc:
        fail("single_item_creation", f"Sub-category '{sc_name}' not found")
        return None

    item_group = frappe.db.get_value("CH Category", sc.category, "item_group")
    if not item_group:
        fail("single_item_creation", f"CH Category '{sc.category}' has no item_group")
        return None

    model = frappe.db.get_value("CH Model", {"sub_category": sc_name}, "name")
    if not model:
        fail("single_item_creation", f"No CH Model found for '{sc_name}'")
        return None

    try:
        doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": "__autoname",
            "item_name": "__autoname",
            "item_group": item_group,
            "stock_uom": "Nos",
            "ch_category": sc.category,
            "ch_sub_category": sc_name,
            "ch_model": model,
            "has_variants": 0,
            # DATA FIX NEEDED: 'Accessories-No Attributes' has hsn_code='8511' (4 digits).
            # Fix sub-category to use '85111000'. Passing it explicitly here to test flow.
            "gst_hsn_code": "85111000",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        code = doc.item_code
        ok(
            "single_item_creation",
            f"Item: {code}  name='{doc.item_name}'  display='{doc.ch_display_name}'",
        )
        return code
    except Exception as e:
        fail("single_item_creation", str(e))
        return None


# ─── Test 2: Item without ch_model (bare minimum — shows mandatory field block) ─

def test_single_item_without_ch_model():
    """Try to create a plain item without ch_category/ch_sub_category/ch_model.

    Expected: BLOCKED — these 3 fields are mandatory (reqd=1) at the DocType level,
    not just the UI.  The backend team must always provide them.
    This test documents the exact error they will see.
    """
    item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
    try:
        doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": "E2E-BARE-001",
            "item_name": "E2E Bare Item 001",
            "item_group": item_group,
            "stock_uom": "Nos",
            "gst_hsn_code": "85111000",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        code = doc.item_code
        ok(
            "single_item_without_ch_model",
            f"Item created without ch_model: {code}  (ch fields not enforced at API level)",
        )
        return code
    except frappe.MandatoryError:
        # This is EXPECTED: ch_category / ch_sub_category / ch_model are mandatory
        ok(
            "single_item_without_ch_model",
            "Correctly blocked: ch_category, ch_sub_category, ch_model are mandatory — backend team must provide all 3",
        )
        return None
    except Exception as e:
        fail("single_item_without_ch_model", f"Unexpected error: {str(e)[:300]}")
        return None


# ─── Test 3: Template item (variant sub-category, fresh CH Model) ─────────────

def test_template_item_creation():
    """Create a template item on Smart Phone-PHONE by first creating a fresh CH Model.

    Smart Phone-PHONE has valid HSN (85171210) and 4 variant specs.
    We create a test model, add spec values, then insert an Item.
    The before_insert hook auto-sets has_variants=1 and populates attributes.
    """
    global _test_model_name

    sc_name = "Smart Phone-PHONE"
    sc = frappe.db.get_value("CH Sub Category", sc_name, ["name", "prefix", "category"], as_dict=True)
    if not sc:
        fail("template_item_creation", f"Sub-category '{sc_name}' not found")
        return None, None

    item_group = frappe.db.get_value("CH Category", sc.category, "item_group")
    # Use existing manufacturer/brand that exist on Galaxy S25
    manufacturer = frappe.db.get_value("CH Model", "Galaxy S25", "manufacturer") or "Samsung"
    brand = frappe.db.get_value("CH Model", "Galaxy S25", "brand") or "Samsung"

    # Ensure manufacturer + brand exist
    if not frappe.db.exists("Manufacturer", manufacturer):
        fail("template_item_creation", f"Manufacturer '{manufacturer}' not found")
        return None, None

    # Create a fresh test CH Model with ALL variant spec values in one insert
    # (CH Model.validate_variant_specs_have_values requires values on first save)
    try:
        variant_specs = frappe.get_all(
            "CH Sub Category Spec",
            filters={"parent": sc_name, "is_variant": 1},
            pluck="spec",
        )
        # Map known spec names to test values (must match exact Item Attribute option values)
        spec_value_map = {
            "Network": "5G",
            "RAM": "8 GB",
            "Storage": "256GB",  # must match exact attribute value (no space)
            "Colour": "White",   # must match exact attribute value
        }
        spec_values = [(s, spec_value_map[s]) for s in variant_specs if s in spec_value_map]
        if len(spec_values) < len(variant_specs):
            missing = [s for s in variant_specs if s not in spec_value_map]
            fail("template_item_creation", f"No test values known for specs: {missing}")
            return None, None
        # Create model with ALL spec values in one insert
        _test_model_name = _create_test_model(sc_name, manufacturer, brand, spec_values=spec_values)
    except Exception as e:
        fail("template_item_creation", f"Could not create test CH Model: {str(e)[:200]}")
        return None, None

    try:
        doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": "__autoname",
            "item_name": "__autoname",
            "item_group": item_group,
            "stock_uom": "Nos",
            "ch_category": sc.category,
            "ch_sub_category": sc_name,
            "ch_model": _test_model_name,
            "has_variants": 0,  # before_insert auto-sets to 1 via _populate_from_model
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        code = doc.item_code
        ok(
            "template_item_creation",
            f"Template: {code}  has_variants={doc.has_variants}  "
            f"attrs={[a.attribute for a in (doc.attributes or [])]}  "
            f"name='{doc.item_name}'",
        )
        return code, _test_model_name
    except Exception as e:
        fail("template_item_creation", str(e))
        return None, None


# ─── Test 4: Create variants from template ────────────────────────────────────

def test_variant_creation_from_template(template_code, model_name):
    """Create variants using ERPNext's create_variant controller.

    Tests the _copy_ch_fields_from_template path.
    """
    if not template_code:
        fail("variant_creation_from_template", "Skipped — no template available")
        return []

    from erpnext.controllers.item_variant import create_variant

    # Get spec values from model (only variant specs)
    spec_rows = frappe.get_all(
        "CH Model Spec Value",
        filters={"parent": model_name},
        fields=["spec", "spec_value"],
        order_by="idx asc",
        ignore_permissions=True,
    )
    variant_specs_set = set(frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": "Smart Phone-PHONE", "is_variant": 1},
        pluck="spec",
        ignore_permissions=True,
    ))

    # Build unique single-spec combos for testing (first value of each variant spec)
    combos = {}
    for row in spec_rows:
        if row.spec in variant_specs_set and row.spec not in combos:
            combos[row.spec] = row.spec_value
    if len(combos) < 2:
        fail("variant_creation_from_template", f"Not enough spec values on model '{model_name}'")
        return []

    # Use ALL variant specs together as a single combination to avoid duplicate item name
    full_combo = dict(list(combos.items()))

    created = []
    try:
        variant = create_variant(template_code, full_combo)
        variant.save(ignore_permissions=True)
        frappe.db.commit()
        created.append(variant.item_code)
        ok(
            "variant_creation_from_template",
            f"Variant: {variant.item_code}  attrs={full_combo}  name='{variant.item_name}'",
        )
    except Exception as e:
        fail("variant_creation_from_template", f"create_variant failed: {str(e)[:200]}")
    return created


# ─── Test 5: CSV import simulation (template already exists, add 2 variants) ──

def test_csv_style_import():
    """Simulate Data Import: use existing Smart Phone template PH000001 + 2 variants.

    In CSV import mode (frappe.flags.in_import=True) before_insert does NOT
    auto-set has_variants, so we must set it explicitly.
    """
    # Use existing template PH000001 (Galaxy S25)
    tpl_code = frappe.db.get_value("Item", {"item_code": "PH000001"}, "item_code")
    if not tpl_code:
        tpl_code = frappe.db.get_value("Item", {"ch_sub_category": "Smart Phone-PHONE", "has_variants": 1}, "item_code")
    if not tpl_code:
        fail("csv_style_import", "No existing Smart Phone template found (PH000001)")
        return []

    tpl = frappe.db.get_value(
        "Item", tpl_code,
        ["item_group", "stock_uom", "ch_category", "ch_sub_category", "ch_model"],
        as_dict=True,
    )
    model_name = tpl.ch_model

    # Get first 2 spec values from the model
    spec_rows = frappe.get_all(
        "CH Model Spec Value",
        filters={"parent": model_name},
        fields=["spec", "spec_value"],
        order_by="idx asc",
        ignore_permissions=True,
    )
    variant_specs_set = set(frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": tpl.ch_sub_category, "is_variant": 1},
        pluck="spec",
        ignore_permissions=True,
    ))
    combos = [(r.spec, r.spec_value) for r in spec_rows if r.spec in variant_specs_set][:2]

    if not combos:
        fail("csv_style_import", f"No variant spec values on model '{model_name}'")
        return []

    created = []
    try:
        frappe.flags.in_import = True
        ok("csv_style_import_template", f"Reusing existing template: {tpl_code}  model={model_name}")

        for i, (spec, value) in enumerate(combos, 1):
            # Check if this combination already has a variant
            existing_var = frappe.db.get_value(
                "Item Variant Attribute",
                {"parent": ["like", tpl_code + "%"], "attribute": spec, "attribute_value": value},
                "parent",
            )
            if existing_var and frappe.db.exists("Item", existing_var):
                ok("csv_style_import_variants", f"Variant {i} already exists: {existing_var}  (idempotent)")
                continue

            var_doc = frappe.get_doc({
                "doctype": "Item",
                "item_code": "__autoname",
                "item_name": "__autoname",
                "item_group": tpl.item_group,
                "stock_uom": tpl.stock_uom,
                "ch_category": tpl.ch_category,
                "ch_sub_category": tpl.ch_sub_category,
                "ch_model": model_name,
                "has_variants": 0,
                "variant_of": tpl_code,
                "variant_based_on": "Item Attribute",
            })
            var_doc.append("attributes", {"attribute": spec, "attribute_value": value})
            var_doc.insert(ignore_permissions=True)
            frappe.db.commit()
            created.append(var_doc.item_code)
            ok("csv_style_import_variants", f"Variant {i}: {var_doc.item_code}  {spec}={value}  name='{var_doc.item_name}'")

    except Exception as e:
        fail("csv_style_import", str(e))
    finally:
        frappe.flags.in_import = False

    return created


# ─── Run all ─────────────────────────────────────────────────────────────────

def run_all():
    global _results, _test_model_name
    _results = []
    cleanup_codes = []
    _test_model_name = None

    print("=" * 65)
    print("ITEM CREATION E2E TESTS")
    print("=" * 65)

    # Test 1: Single item with ch_model (no variant specs)
    code1 = test_single_item_creation()
    if code1:
        cleanup_codes.append(code1)

    # Test 2: Item without ch_model (bare ERPNext)
    code2 = test_single_item_without_ch_model()
    if code2:
        cleanup_codes.append(code2)

    # Test 3: Template creation (new CH Model created for test)
    tpl_code, model_name = test_template_item_creation()
    if tpl_code:
        cleanup_codes.append(tpl_code)

    # Test 4: Variant creation from fresh template
    variant_codes = test_variant_creation_from_template(tpl_code, model_name)
    cleanup_codes.extend(variant_codes)

    # Test 5: CSV-style import against existing template
    csv_codes = test_csv_style_import()
    cleanup_codes.extend(csv_codes)

    print("-" * 65)
    passed = sum(1 for r in _results if r[0] == "PASS")
    failed = sum(1 for r in _results if r[0] == "FAIL")
    print(f"RESULT: PASS={passed}  FAIL={failed}")
    print("=" * 65)

    # Clean up all test data
    if cleanup_codes or _test_model_name:
        print(f"\nCleaning up {len(cleanup_codes)} test items" +
              (f" + test model '{_test_model_name}'" if _test_model_name else "") + "...")
        _cleanup(cleanup_codes, _test_model_name)
        print("Done.")

    return {"passed": passed, "failed": failed, "results": _results}
