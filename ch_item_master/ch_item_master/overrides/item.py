# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Document event handlers for ERPNext Item.
Hooked via hooks.py doc_events.

Works with ERPNext's native variant system:
  - Template items have has_variants=1 + ch_model set
  - Variant items have variant_of set (created by ERPNext)

- before_insert: auto-generate item_code for template items
- before_save:   keep ch_display_name / item_name in sync
"""

import frappe
from frappe import _
from frappe.utils import cint

from ch_item_master.ch_item_master.api import generate_item_name
from ch_item_master.ch_item_master.utils import (
    _next_item_code,
    _group_model_spec_values,
    _get_spec_selectors,
    _get_property_specs,
)
from ch_item_master.ch_item_master.exceptions import (
    DuplicateItemNameError,
    DuplicateTemplateError,
    MissingPrefixError,
)


def _populate_from_model(doc):
    """Auto-populate Item fields from the linked CH Model.

    Called at the top of before_insert so that items created via the
    model-driven quick entry (which only sends ch_model + stock_uom)
    get all derived fields filled in before the rest of the pipeline
    runs (item_code generation, duplicate checks, etc.).

    Only fills fields that are currently empty/unset so that values
    explicitly set by the user (e.g. via full-form) are preserved.
    """
    if not doc.ch_model or doc.variant_of:
        return

    model_data = frappe.db.get_value(
        "CH Model", doc.ch_model,
        ["sub_category", "manufacturer", "brand"],
        as_dict=True,
    )
    if not model_data:
        return

    # ── Hierarchy ──────────────────────────────────────────────────────
    if not doc.ch_sub_category and model_data.sub_category:
        doc.ch_sub_category = model_data.sub_category

    if doc.ch_sub_category and not doc.ch_category:
        doc.ch_category = frappe.db.get_value(
            "CH Sub Category", doc.ch_sub_category, "category"
        ) or ""

    # ── Core mandatory fields ─────────────────────────────────────────
    if not doc.item_group and doc.ch_category:
        doc.item_group = frappe.db.get_value(
            "CH Category", doc.ch_category, "item_group"
        ) or ""

    if not doc.get("gst_hsn_code") and doc.ch_sub_category:
        doc.gst_hsn_code = frappe.db.get_value(
            "CH Sub Category", doc.ch_sub_category, "hsn_code"
        ) or ""

    # ── Variant setup ─────────────────────────────────────────────────
    grouped_specs = _group_model_spec_values(doc.ch_model)
    spec_selectors = _get_spec_selectors(
        doc.ch_sub_category, doc.ch_model, grouped=grouped_specs
    )

    # Only auto-set has_variants when not explicitly provided (e.g. quick
    # entry flow).  During data import the user controls has_variants.
    if spec_selectors and not doc.has_variants and not frappe.flags.in_import:
        doc.has_variants = 1
        doc.variant_based_on = "Item Attribute"

    # Populate attributes if template but table is empty
    if doc.has_variants and not doc.get("attributes"):
        doc.set("attributes", [])
        for s in spec_selectors:
            doc.append("attributes", {"attribute": s["spec"]})

    # ── Property specs (non-variant) ──────────────────────────────────
    if not doc.get("ch_spec_values"):
        property_specs = _get_property_specs(
            doc.ch_sub_category, doc.ch_model, grouped=grouped_specs
        )
        if property_specs:
            doc.set("ch_spec_values", [])
            for ps in property_specs:
                doc.append("ch_spec_values", {"spec": ps["spec"]})

    # ── Model features ────────────────────────────────────────────────
    if not doc.get("ch_model_features"):
        features = frappe.get_all(
            "CH Model Feature",
            filters={"parent": doc.ch_model, "parenttype": "CH Model"},
            fields=["feature_group", "feature_name", "feature_value"],
            order_by="idx asc",
        )
        if features:
            doc.set("ch_model_features", [])
            for f in features:
                doc.append("ch_model_features", {
                    "feature_group": f.feature_group,
                    "feature_name": f.feature_name,
                    "feature_value": f.feature_value,
                })


def _get_model_fields(doc):
    """Return (manufacturer, brand) from the linked CH Model."""
    if not doc.ch_model:
        return None, None
    fields = frappe.db.get_value("CH Model", doc.ch_model, ["manufacturer", "brand"], as_dict=True)
    return (fields.manufacturer, fields.brand) if fields else (None, None)


def _sync_model_features(doc):
    """Sync model features from CH Model → Item on every save.

    Re-reads the CH Model Feature child table and replaces the Item's
    ch_model_features table.  This keeps Items in sync when features
    are edited on the model after the Item was created.
    """
    features = frappe.get_all(
        "CH Model Feature",
        filters={"parent": doc.ch_model, "parenttype": "CH Model"},
        fields=["feature_group", "feature_name", "feature_value"],
        order_by="idx asc",
    )
    doc.set("ch_model_features", [])
    for f in features:
        doc.append("ch_model_features", {
            "feature_group": f.feature_group,
            "feature_name": f.feature_name,
            "feature_value": f.feature_value,
        })


def _get_spec_values_from_attributes(doc):
    """Extract attribute values from ERPNext's native attributes table.

    Variant items created by ERPNext store their spec values in the
    'attributes' child table (Item Variant Attribute), not ch_spec_values.
    """
    return [
        {"spec": row.attribute, "spec_value": row.attribute_value}
        for row in (doc.attributes or [])
        if row.attribute and row.attribute_value
    ]


def _category_allows_custom_name(doc):
    """Return True if doc's CH Category has allow_custom_item_name=1.

    The flag only applies to simple (non-variant) items. Templates and
    variants ALWAYS auto-generate names regardless of the flag because:
      - templates anchor variant naming
      - variants need spec/attribute suffixes for uniqueness
    """
    if doc.has_variants or doc.variant_of:
        return False
    if not doc.ch_category:
        return False
    return bool(
        frappe.db.get_value(
            "CH Category", doc.ch_category, "allow_custom_item_name"
        )
    )


def _should_preserve_user_name(doc, generated_name, prev_display=None):
    """Decide whether to keep doc.item_name (user-supplied) instead of overwriting.

    Preserve only when ALL of:
      - Category flag allows custom names (simple item only).
      - doc.item_name is set, not the '__autoname' placeholder, not blank.
      - doc.item_name differs from the freshly generated name (else it's
        already auto, keep refresh-on-spec-edit semantics).
      - doc.item_name does not match the previously stored generated value
        (prev_display) — i.e. the user actually typed something custom
        rather than just re-saving an auto-generated row.
    """
    if not _category_allows_custom_name(doc):
        return False
    current = (doc.item_name or "").strip()
    if not current or current.lower() == "__autoname":
        return False
    if generated_name and current == generated_name:
        return False
    prev = (prev_display or "").strip()
    if prev and current == prev:
        return False
    return True

def before_insert(doc, method=None):
    """Auto-generate item_code for CH items.

    For variants (variant_of is set), ERPNext handles item_code generation
    via make_variant_item_code — we just ensure CH fields are copied.
    For simple items (non-variant sub-category), create a standalone item.
    For templates (variant sub-category), create a template item.
    """
    # Handle variants FIRST — they may not have ch_sub_category yet
    if doc.variant_of:
        _copy_ch_fields_from_template(doc)
        return

    # Auto-populate fields from CH Model (supports quick entry flow
    # where user only selects a model and everything else is derived)
    _populate_from_model(doc)

    # Auto-detect variant: if this model's template already exists and
    # _populate_from_model auto-set has_variants=1, this MUST be a variant
    # (one template per model is strict).  Handle import/API rows where the
    # variant_of column was omitted by routing to the variant code-path.
    if not doc.variant_of and doc.ch_model and doc.has_variants:
        existing_tpl = frappe.db.get_value(
            "Item", {"ch_model": doc.ch_model, "has_variants": 1}, "name"
        )
        if existing_tpl:
            doc.variant_of = existing_tpl
            doc.has_variants = 0
            _copy_ch_fields_from_template(doc)
            return

    placeholder_code = (doc.item_code or "").strip().lower() == "__autoname"

    # Template/simple items — need ch_sub_category to proceed
    if not doc.ch_sub_category:
        if placeholder_code or doc.ch_model:
            frappe.throw(
                _(
                    "Unable to generate Item Code. Please select a valid CH Sub Category/Model "
                    "and ensure Prefix is configured in CH Sub Category."
                )
            )
        return

    # Only block duplicate templates for variant sub-categories
    if doc.has_variants:
        _check_duplicate_template(doc)

    # Auto-generate when item_code is blank OR UI placeholder '__autoname'.
    if not doc.item_code or placeholder_code:
        _set_item_code(doc)
    _set_item_name(doc)
    # Duplicate name check is handled by before_save which always runs after


def _copy_ch_fields_from_template(doc):
    """Copy CH custom fields from the template item to the variant.

    Uses targeted queries instead of loading the full template doc to avoid
    unnecessary overhead (child tables, computed fields, etc.).
    Also copies gst_hsn_code so India Compliance validation passes for variants.
    """
    ch_fields = frappe.db.get_value(
        "Item", doc.variant_of,
        ["ch_model", "ch_sub_category", "ch_category", "gst_hsn_code"],
        as_dict=True,
    )
    if not ch_fields:
        return

    for field in ("ch_model", "ch_sub_category", "ch_category"):
        if not getattr(doc, field, None) and ch_fields.get(field):
            setattr(doc, field, ch_fields[field])

    # Copy gst_hsn_code from template so India Compliance HSN validation passes
    if not doc.get("gst_hsn_code") and ch_fields.get("gst_hsn_code"):
        doc.gst_hsn_code = ch_fields["gst_hsn_code"]

    # Copy property spec values from the saved template via targeted query
    doc.set("ch_spec_values", [])
    for row in frappe.get_all(
        "CH Item Spec Value",
        filters={"parent": doc.variant_of, "parenttype": "Item"},
        fields=["spec", "spec_value"],
        order_by="idx asc",
    ):
        doc.append("ch_spec_values", {"spec": row.spec, "spec_value": row.spec_value})

    # Copy model features from the template
    doc.set("ch_model_features", [])
    for row in frappe.get_all(
        "CH Item Feature",
        filters={"parent": doc.variant_of, "parenttype": "Item"},
        fields=["feature_group", "feature_name", "feature_value"],
        order_by="idx asc",
    ):
        doc.append("ch_model_features", {
            "feature_group": row.feature_group,
            "feature_name": row.feature_name,
            "feature_value": row.feature_value,
        })


def _validate_ch_spec_values(doc):
    """Validate ch_spec_values on the Item.

    1. Property specs (is_variant=0) must have at least one value.
    2. No duplicate spec entries allowed in ch_spec_values.
    """
    if not doc.ch_sub_category:
        return

    # Get property specs (non-variant) for this sub category
    property_specs = frappe.get_all(
        "CH Sub Category Spec",
        filters={
            "parent": doc.ch_sub_category,
            "parenttype": "CH Sub Category",
            "is_variant": 0,
        },
        pluck="spec",
        ignore_permissions=True,
    )

    # Check for duplicate specs in ch_spec_values
    seen_specs = set()
    for row in (doc.ch_spec_values or []):
        if row.spec in seen_specs:
            frappe.throw(
                _("Row #{0}: Duplicate spec {1} in Spec Values. "
                  "Each spec should appear only once."
                ).format(row.idx, frappe.bold(row.spec)),
                title=_("Duplicate Spec Value"),
            )
        seen_specs.add(row.spec)

    # For non-variant items (templates or variants with property specs),
    # require all property specs to have a value
    if property_specs:
        filled_specs = {row.spec for row in (doc.ch_spec_values or []) if row.spec_value}
        missing = [s for s in property_specs if s not in filled_specs]
        if missing:
            frappe.throw(
                _("Property spec(s) {0} require a value. "
                  "Add them in the Spec Values table."
                ).format(", ".join(frappe.bold(s) for s in missing)),
                title=_("Missing Property Spec Values"),
            )


def before_save(doc, method=None):
    """Keep ch_display_name in sync and populate master IDs on every save."""
    _populate_master_ids(doc)

    if not doc.ch_sub_category or not doc.ch_model:
        return

    _validate_ch_spec_values(doc)

    # Sync model features from CH Model on every save (keeps Item in sync
    # if features were edited on the model after Item was created)
    _sync_model_features(doc)

    manufacturer, brand = _get_model_fields(doc)

    # For variants, get spec values from ERPNext's attributes table
    # AND merge in ch_spec_values (property specs like Colour that don't
    # affect pricing but may be marked "Include in Name")
    if doc.variant_of:
        spec_values = _get_spec_values_from_attributes(doc)
        # Merge ch_spec_values (non-pricing / property specs) so that specs
        # like Colour with in_item_name=1 but affects_price=0 are included
        attr_specs = {sv["spec"] for sv in spec_values}
        for row in (doc.ch_spec_values or []):
            if row.spec not in attr_specs and row.spec_value:
                spec_values.append({"spec": row.spec, "spec_value": row.spec_value})
    elif not doc.has_variants:
        # Simple (non-variant) item — include spec values in name just like variants
        spec_values = [
            {"spec": row.spec, "spec_value": row.spec_value}
            for row in (doc.ch_spec_values or [])
            if row.spec_value
        ]
    else:
        spec_values = []  # Template item — no spec values in name

    display_name = generate_item_name(
        sub_category=doc.ch_sub_category,
        manufacturer=manufacturer,
        brand=brand,
        model=doc.ch_model,
        spec_values=spec_values,
    )

    # Append item type suffix (Refurbished, Pre-Owned, etc.) to keep names unique
    if display_name and doc.get("ch_item_type"):
        display_name = f"{display_name} {doc.get('ch_item_type')}"

    # Capture previous persisted display name BEFORE overwriting — used by
    # the preserve-user-name check to detect "auto-managed" rows.
    prev_display = (doc.get("ch_display_name") or "").strip()

    doc.ch_display_name = display_name

    # Safety: if variant name is identical to the template name (specs not
    # appended — e.g. sub-category has no in_item_name specs configured),
    # force-append attribute values so the variant is distinguishable.
    if display_name and doc.variant_of:
        tpl_name = frappe.db.get_value("Item", doc.variant_of, "item_name")
        if display_name == tpl_name:
            if doc.attributes:
                attr_parts = [str(r.attribute_value).strip() for r in doc.attributes if r.attribute_value]
                if attr_parts:
                    display_name = f"{display_name} {' '.join(attr_parts)}"
                    doc.ch_display_name = display_name
            else:
                # No attribute rows populated (e.g. Data Import without the
                # attributes child table in the CSV).  Use the item_code suffix
                # to keep the variant name unique and human-readable.
                code_suffix = (doc.item_code or "").replace(doc.variant_of or "", "").strip("-_ ")
                if code_suffix:
                    display_name = f"{display_name} [{code_suffix}]"
                    doc.ch_display_name = display_name

    if display_name:
        preserve = _should_preserve_user_name(doc, display_name, prev_display=prev_display)
        if not preserve:
            doc.item_name = display_name
            # Keep description in sync — replaces hyphenated default set by ERPNext variant creation
            doc.description = display_name
        else:
            # User-supplied custom name on a category that opted in.
            # Sync description only when description was blank or matched the
            # previously generated label (i.e. user never customized it).
            current_desc = (doc.description or "").strip()
            if not current_desc or current_desc == prev_display:
                doc.description = doc.item_name

    # Validate generated name is unique (on every save)
    _check_duplicate_item_name(doc)


def _check_duplicate_template(doc):
    """Block creation of a second template for the same CH Model.

    Only one template item per model is allowed — all variants branch from it.
    """
    if not doc.ch_model:
        return

    # doc.name may be blank before the first INSERT (autoname not yet applied).
    # Fall back to item_code so re-importing the same template row is allowed.
    exclude_name = doc.name or doc.item_code or ""
    existing = frappe.db.get_value(
        "Item",
        {"ch_model": doc.ch_model, "has_variants": 1, "name": ("!=", exclude_name)},
        ["name", "item_name"],
        as_dict=True,
    )
    if existing:
        frappe.throw(
            _("A template already exists for model {0}: {1}").format(
                frappe.bold(doc.ch_model),
                f'<a href="/desk/item/{existing.name}">{existing.name}</a>',
            ),
            title=_("Duplicate Template"),
            exc=DuplicateTemplateError,
        )


def _check_duplicate_item_name(doc):
    """Block any item (template or variant) with a duplicate item_name.

    item_name is auto-generated from model + specs; a duplicate means
    the same combination already exists.
    For variants, also exclude the template from the check — the template's
    base name is expected to overlap (variant adds spec suffixes).
    """
    name_to_check = doc.item_name
    if not name_to_check or name_to_check == "__autoname":
        return

    exclude_names = [doc.name or ""]
    if doc.variant_of:
        exclude_names.append(doc.variant_of)

    existing = frappe.db.get_value(
        "Item",
        {"item_name": name_to_check, "name": ("not in", exclude_names)},
        "name",
    )
    if existing:
        frappe.throw(
            _("An item with the name {0} already exists: {1}").format(
                frappe.bold(name_to_check),
                f'<a href="/desk/item/{existing}">{existing}</a>',
            ),
            title=_("Duplicate Item Name"),
            exc=DuplicateItemNameError,
        )


def _set_item_code(doc):
    """Set item_code from prefix + next sequence number.

    Always regenerates at insert time with a database-level advisory lock so
    two simultaneous saves never produce the same code.
    """
    prefix = frappe.db.get_value("CH Sub Category", doc.ch_sub_category, "prefix")
    if not prefix:
        frappe.throw(
            _("Sub Category {0} has no Prefix configured. Please set a Prefix before creating items.").format(
                frappe.bold(doc.ch_sub_category)
            ),
            exc=MissingPrefixError,
        )

    prefix = prefix.strip().upper()

    lock_name = f"ch_item_code_{prefix}"
    # Increased timeout to 30 seconds for high-concurrency scenarios
    lock_ok = frappe.db.sql("SELECT GET_LOCK(%s, 30)", lock_name)[0][0]
    if not lock_ok:
        frappe.log_error(f"Lock acquisition timeout for {lock_name}", "Item Code Lock Timeout")
        frappe.throw(_("System busy generating item codes. Please retry in a moment."), title=_("Validation Error"))

    try:
        doc.item_code = _next_item_code(prefix)
    except Exception as e:
        frappe.log_error(f"Error generating item code for prefix {prefix}: {str(e)}", "Item Code Generation Error")
        raise
    finally:
        # Guaranteed cleanup — always release the lock
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name)


def _set_item_name(doc):
    """Set item_name at insert time.

    For templates (has_variants=1): name without spec values.
    For simple items (has_variants=0, no variant_of): include spec values.
    """
    if not doc.ch_model:
        return

    manufacturer, brand = _get_model_fields(doc)

    # Simple items include their spec values in the name
    if not doc.has_variants:
        spec_values = [
            {"spec": row.spec, "spec_value": row.spec_value}
            for row in (doc.ch_spec_values or [])
            if row.spec_value
        ]
    else:
        spec_values = []  # Template — no spec values

    generated = generate_item_name(
        sub_category=doc.ch_sub_category,
        manufacturer=manufacturer,
        brand=brand,
        model=doc.ch_model,
        spec_values=spec_values,
    )

    # Append item type suffix (Refurbished, Pre-Owned, etc.) to keep names unique
    if generated and doc.get("ch_item_type"):
        generated = f"{generated} {doc.get('ch_item_type')}"

    if generated:
        # Always set ch_display_name to the generated descriptive label
        doc.ch_display_name = generated
        # Preserve user-supplied item_name only when category opts in AND
        # the user actually typed a real name (not blank/__autoname).
        if not _should_preserve_user_name(doc, generated):
            doc.item_name = generated


def _populate_master_ids(doc):
    """Copy numeric IDs from linked master records into the Item.

    Runs on every save so that IDs stay in sync if the linked model/sub-category
    changes.  Uses a single JOIN query instead of 6 separate lookups.

    Fields populated:
      - ch_brand_id       ← Brand.brand_id (via CH Model.brand)
      - ch_manufacturer_id ← Manufacturer.manufacturer_id (via CH Model.manufacturer)
      - ch_sub_category_id ← CH Sub Category.sub_category_id
      - ch_model_id        ← CH Model.model_id
      - ch_category_id     ← CH Category.category_id
      - ch_item_group_id   ← Item Group.item_group_id
    """
    # Reset all IDs first — if links are cleared, IDs should be cleared too
    doc.ch_brand_id = 0
    doc.ch_manufacturer_id = 0
    doc.ch_sub_category_id = 0
    doc.ch_model_id = 0
    doc.ch_category_id = 0
    doc.ch_item_group_id = 0

    row = frappe.db.sql("""
        SELECT
            cat.category_id     AS category_id,
            ig.item_group_id    AS item_group_id,
            sc.sub_category_id  AS sub_category_id,
            m.model_id          AS model_id,
            b.brand_id          AS brand_id,
            mfr.manufacturer_id AS manufacturer_id
        FROM (SELECT 1) AS dummy
        LEFT JOIN `tabCH Category`      cat ON cat.name = %(category)s
        LEFT JOIN `tabItem Group`        ig ON ig.name  = %(item_group)s
        LEFT JOIN `tabCH Sub Category`   sc ON sc.name  = %(sub_category)s
        LEFT JOIN `tabCH Model`           m ON m.name   = %(model)s
        LEFT JOIN `tabBrand`              b ON b.name   = m.brand
        LEFT JOIN `tabManufacturer`     mfr ON mfr.name = m.manufacturer
    """, {
        "category": doc.ch_category or "",
        "item_group": doc.item_group or "",
        "sub_category": doc.ch_sub_category or "",
        "model": doc.ch_model or "",
    }, as_dict=True)

    if row:
        r = row[0]
        doc.ch_category_id = cint(r.category_id)
        doc.ch_item_group_id = cint(r.item_group_id)
        doc.ch_sub_category_id = cint(r.sub_category_id)
        doc.ch_model_id = cint(r.model_id)
        doc.ch_brand_id = cint(r.brand_id)
        doc.ch_manufacturer_id = cint(r.manufacturer_id)
