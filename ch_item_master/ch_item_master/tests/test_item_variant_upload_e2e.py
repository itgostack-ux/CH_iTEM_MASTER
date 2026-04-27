"""
E2E Tests for Item Variant Upload

Tests the three failure modes that cause "template already exists" or name
errors when importing variant items:

  Case 1: Import variant WITH variant_of but WITHOUT attribute rows
           → item_name collapses to template name → DuplicateItemNameError
  Case 2: Import variant WITHOUT variant_of (ch_model set)
           → falls into template path → DuplicateTemplateError
  Case 3: Re-import template row that already exists (idempotent import)
           → should be allowed if item_code matches

Run:
  bench --site erpnext.local execute \\
    ch_item_master.ch_item_master.tests.test_item_variant_upload_e2e.run_all
"""

import frappe
from frappe import _

_results = []


def ok(name, detail=""):
    _results.append(("PASS", name, detail))
    print(f"PASS  {name}" + (f"  ({detail})" if detail else ""))


def fail(name, detail=""):
    _results.append(("FAIL", name, detail))
    print(f"FAIL  {name}" + (f"  ({detail})" if detail else ""))


def _get_test_template():
    """Find a template with at least one variant for testing."""
    tpl = frappe.db.sql("""
        SELECT i.item_code, i.item_name, i.ch_model, i.ch_sub_category
        FROM tabItem i
        WHERE i.has_variants = 1
          AND i.ch_model IS NOT NULL AND i.ch_model != ''
          AND EXISTS (
              SELECT 1 FROM tabItem v WHERE v.variant_of = i.item_code
          )
        LIMIT 1
    """, as_dict=True)
    if tpl:
        return tpl[0]
    # Fallback: any template even without variants
    tpl2 = frappe.db.sql("""
        SELECT item_code, item_name, ch_model, ch_sub_category
        FROM tabItem
        WHERE has_variants = 1
          AND ch_model IS NOT NULL AND ch_model != ''
        LIMIT 1
    """, as_dict=True)
    return tpl2[0] if tpl2 else None


def _get_first_variant(template_code):
    """Get the first variant of a template."""
    return frappe.db.get_value(
        "Item",
        {"variant_of": template_code},
        ["item_code", "item_name", "ch_model", "variant_of"],
        as_dict=True,
    )


# ─── Case 1: Variant import WITH variant_of but WITHOUT attributes ───────────
def test_variant_import_with_variant_of_no_attributes():
    """
    When a variant is imported via Data Import CSV and attributes child rows
    are missing, _get_spec_values_from_attributes returns [] and the generated
    item_name equals the template's name. The safety fallback also fails because
    doc.attributes is empty. Result: _check_duplicate_item_name fires.

    Expected: should NOT raise. The variant should either keep its CSV item_name
    or get a unique generated name from its item_code suffix.
    """
    tpl = _get_test_template()
    if not tpl:
        fail("variant_import_no_attributes", "No template available for test")
        return

    variant = _get_first_variant(tpl.item_code)
    if not variant:
        fail("variant_import_no_attributes", "No existing variant to test with")
        return

    # Simulate: load the variant doc, clear its attributes, re-trigger before_save
    try:
        doc = frappe.get_doc("Item", variant.item_code)
        original_attrs = list(doc.attributes or [])
        original_name = doc.item_name

        # Temporarily clear attributes (simulate import without attribute rows)
        doc.set("attributes", [])

        from ch_item_master.ch_item_master.overrides.item import before_save
        before_save(doc, method=None)

        restored_name = doc.item_name

        # Restore
        doc.set("attributes", original_attrs)

        if restored_name == tpl.item_name:
            fail(
                "variant_import_no_attributes",
                f"Name collapsed to template name: '{restored_name}'. "
                f"Duplicate name error will fire on save. Original: '{original_name}'",
            )
        else:
            ok(
                "variant_import_no_attributes",
                f"Name preserved/derived: '{restored_name}'",
            )

    except Exception as e:
        fail("variant_import_no_attributes", str(e))


# ─── Case 2: Variant import WITHOUT variant_of ───────────────────────────────
def test_variant_import_without_variant_of():
    """
    When a variant CSV row doesn't have 'variant_of' column filled, the doc
    has doc.variant_of = '' (blank). before_insert doesn't take the early return
    path. _populate_from_model may set has_variants=1, then _check_duplicate_template
    fires: "A template already exists for model X".

    Expected: before_insert should detect a template already exists for this
    ch_model and auto-set doc.variant_of, OR should give a clear error telling
    the user to include the variant_of column.
    """
    tpl = _get_test_template()
    if not tpl:
        fail("variant_import_without_variant_of", "No template available for test")
        return

    try:
        doc = frappe.get_doc({
            "doctype": "Item",
            "ch_model": tpl.ch_model,
            "ch_sub_category": tpl.ch_sub_category,
            "has_variants": 0,
            "variant_of": "",
            "stock_uom": "Nos",
            "item_group": "All Item Groups",
        })

        from ch_item_master.ch_item_master.overrides.item import before_insert
        before_insert(doc, method=None)

        if doc.variant_of:
            ok(
                "variant_import_without_variant_of",
                f"Auto-detected: variant_of set to '{doc.variant_of}'",
            )
        else:
            fail(
                "variant_import_without_variant_of",
                "variant_of was NOT auto-set. User will get 'template already exists' error.",
            )

    except frappe.exceptions.ValidationError as e:
        if "template already exists" in str(e).lower() or "duplicate template" in str(e).lower():
            fail(
                "variant_import_without_variant_of",
                f"Got expected-but-wrong error: {str(e)[:120]}",
            )
        else:
            fail("variant_import_without_variant_of", f"Unexpected error: {str(e)[:120]}")
    except Exception as e:
        fail("variant_import_without_variant_of", f"Unexpected exception: {str(e)[:120]}")


# ─── Case 3: Re-import existing template (idempotent) ────────────────────────
def test_template_reimport_idempotent():
    """
    When the same CSV is imported a second time (e.g., adding new variants while
    keeping template row), the template row is re-processed. Since item_code
    matches an existing template for the same ch_model, it should NOT raise
    "template already exists" — it IS the same template.

    Expected: _check_duplicate_template should be a no-op when doc.name
    (the item_code from the CSV) exactly matches the existing template.
    """
    tpl = _get_test_template()
    if not tpl:
        fail("template_reimport_idempotent", "No template available for test")
        return

    try:
        doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": tpl.item_code,  # Same item_code as existing template
            "ch_model": tpl.ch_model,
            "ch_sub_category": tpl.ch_sub_category,
            "has_variants": 1,
            "variant_of": "",
            "stock_uom": "Nos",
            "item_group": "All Item Groups",
        })

        from ch_item_master.ch_item_master.overrides.item import _check_duplicate_template
        _check_duplicate_template(doc)

        ok("template_reimport_idempotent", f"Re-import of {tpl.item_code} passes duplicate check")

    except frappe.exceptions.ValidationError as e:
        if "template already exists" in str(e).lower() or "duplicate template" in str(e).lower():
            fail(
                "template_reimport_idempotent",
                f"Re-importing existing template raises 'template already exists'. "
                f"Idempotent import broken. Error: {str(e)[:120]}",
            )
        else:
            fail("template_reimport_idempotent", f"Unexpected error: {str(e)[:120]}")
    except Exception as e:
        fail("template_reimport_idempotent", f"Exception: {str(e)[:120]}")


# ─── Case 4: Check variant name derivation when attributes ARE present ───────
def test_variant_name_with_attributes():
    """
    When a variant has attributes populated (normal case), item_name should
    include the attribute values and be different from the template's item_name.
    """
    tpl = _get_test_template()
    if not tpl:
        fail("variant_name_with_attributes", "No template available for test")
        return

    variant = _get_first_variant(tpl.item_code)
    if not variant:
        fail("variant_name_with_attributes", "No existing variant to test with")
        return

    doc = frappe.get_doc("Item", variant.item_code)

    if not doc.attributes:
        fail("variant_name_with_attributes", "Variant has no attributes — cannot test name derivation")
        return

    try:
        from ch_item_master.ch_item_master.overrides.item import before_save
        before_save(doc, method=None)

        if doc.item_name == tpl.item_name:
            fail(
                "variant_name_with_attributes",
                f"Variant name '{doc.item_name}' is same as template '{tpl.item_name}'. Name not derived from attributes.",
            )
        elif any(str(a.attribute_value).lower() in doc.item_name.lower() for a in doc.attributes if a.attribute_value):
            ok("variant_name_with_attributes", f"Variant name: '{doc.item_name}'")
        else:
            fail(
                "variant_name_with_attributes",
                f"Variant name '{doc.item_name}' doesn't include attribute values. "
                f"Attributes: {[(a.attribute, a.attribute_value) for a in doc.attributes]}",
            )
    except Exception as e:
        fail("variant_name_with_attributes", str(e))


# ─── Case 5: in_import flag presence check ───────────────────────────────────
def test_in_import_flag_skips_has_variants_auto_set():
    """
    When frappe.flags.in_import = True, _populate_from_model should NOT auto-set
    has_variants=1 (the user controls this from their CSV).
    """
    tpl = _get_test_template()
    if not tpl:
        fail("in_import_flag_test", "No template available for test")
        return

    try:
        frappe.flags.in_import = True

        doc = frappe.get_doc({
            "doctype": "Item",
            "ch_model": tpl.ch_model,
            "ch_sub_category": tpl.ch_sub_category,
            "has_variants": 0,
            "variant_of": "",
            "stock_uom": "Nos",
            "item_group": "All Item Groups",
        })

        from ch_item_master.ch_item_master.overrides.item import _populate_from_model
        _populate_from_model(doc)

        if doc.has_variants == 0:
            ok("in_import_flag_test", "in_import=True correctly prevents auto-set of has_variants")
        else:
            fail("in_import_flag_test", f"has_variants was set to {doc.has_variants} despite in_import=True")

    except Exception as e:
        fail("in_import_flag_test", str(e))
    finally:
        frappe.flags.in_import = False


def run_all():
    print("\n" + "=" * 65)
    print("ITEM VARIANT UPLOAD E2E TESTS")
    print("=" * 65)

    _results.clear()

    tests = [
        test_variant_import_with_variant_of_no_attributes,
        test_variant_import_without_variant_of,
        test_template_reimport_idempotent,
        test_variant_name_with_attributes,
        test_in_import_flag_skips_has_variants_auto_set,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            fail(t.__name__, f"Uncaught: {str(e)[:200]}")

    passed = sum(1 for r in _results if r[0] == "PASS")
    failed = sum(1 for r in _results if r[0] == "FAIL")
    print("\n" + "-" * 65)
    print(f"RESULT: PASS={passed}  FAIL={failed}")
    print("=" * 65)
    return {"passed": passed, "failed": failed, "results": _results}
