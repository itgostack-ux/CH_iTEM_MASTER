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

from ch_item_master.ch_item_master.api import (
    _next_item_code,
    generate_item_name,
)


def _get_model_fields(doc):
    """Return (manufacturer, brand) from the linked CH Model."""
    if not doc.ch_model:
        return None, None
    fields = frappe.db.get_value("CH Model", doc.ch_model, ["manufacturer", "brand"], as_dict=True)
    return (fields.manufacturer, fields.brand) if fields else (None, None)


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

    # Template/simple items — need ch_sub_category to proceed
    if not doc.ch_sub_category:
        return

    # Only block duplicate templates for variant sub-categories
    if doc.has_variants:
        _check_duplicate_template(doc)

    _set_item_code(doc)
    _set_item_name(doc)
    # Duplicate name check is handled by before_save which always runs after


def _copy_ch_fields_from_template(doc):
    """Copy CH custom fields from the template item to the variant.

    Uses targeted queries instead of loading the full template doc to avoid
    unnecessary overhead (child tables, computed fields, etc.).
    """
    ch_fields = frappe.db.get_value(
        "Item", doc.variant_of,
        ["ch_model", "ch_sub_category", "ch_category"],
        as_dict=True,
    )
    if not ch_fields:
        return

    for field in ("ch_model", "ch_sub_category", "ch_category"):
        if not getattr(doc, field, None) and ch_fields.get(field):
            setattr(doc, field, ch_fields[field])

    # Copy property spec values from the saved template via targeted query
    doc.set("ch_spec_values", [])
    for row in frappe.get_all(
        "CH Item Spec Value",
        filters={"parent": doc.variant_of, "parenttype": "Item"},
        fields=["spec", "spec_value"],
        order_by="idx asc",
    ):
        doc.append("ch_spec_values", {"spec": row.spec, "spec_value": row.spec_value})


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
    """Keep ch_display_name in sync on every save."""
    if not doc.ch_sub_category or not doc.ch_model:
        return

    _validate_ch_spec_values(doc)

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
    else:
        spec_values = []  # Template or simple item — no spec values in name

    display_name = generate_item_name(
        sub_category=doc.ch_sub_category,
        manufacturer=manufacturer,
        brand=brand,
        model=doc.ch_model,
        spec_values=spec_values,
    )
    doc.ch_display_name = display_name

    if display_name:
        doc.item_name = display_name

    # Validate generated name is unique (on every save)
    _check_duplicate_item_name(doc)


def _check_duplicate_template(doc):
    """Block creation of a second template for the same CH Model.

    Only one template item per model is allowed — all variants branch from it.
    """
    if not doc.ch_model:
        return

    existing = frappe.db.get_value(
        "Item",
        {"ch_model": doc.ch_model, "has_variants": 1, "name": ("!=", doc.name or "")},
        ["name", "item_name"],
        as_dict=True,
    )
    if existing:
        frappe.throw(
            _("A template already exists for model {0}: {1}").format(
                frappe.bold(doc.ch_model),
                f'<a href="/app/item/{existing.name}">{existing.name}</a>',
            ),
            title=_("Duplicate Template"),
        )


def _check_duplicate_item_name(doc):
    """Block any item (template or variant) with a duplicate item_name.

    item_name is auto-generated from model + specs; a duplicate means
    the same combination already exists.
    """
    name_to_check = doc.item_name
    if not name_to_check or name_to_check == "__autoname":
        return

    existing = frappe.db.get_value(
        "Item",
        {"item_name": name_to_check, "name": ("!=", doc.name or "")},
        "name",
    )
    if existing:
        frappe.throw(
            _("An item with the name {0} already exists: {1}").format(
                frappe.bold(name_to_check),
                f'<a href="/app/item/{existing}">{existing}</a>',
            ),
            title=_("Duplicate Item Name"),
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
            )
        )

    prefix = prefix.strip().upper()

    lock_name = f"ch_item_code_{prefix}"
    # Increased timeout to 30 seconds for high-concurrency scenarios
    lock_ok = frappe.db.sql("SELECT GET_LOCK(%s, 30)", lock_name)[0][0]
    if not lock_ok:
        frappe.log_error(f"Lock acquisition timeout for {lock_name}", "Item Code Lock Timeout")
        frappe.throw(_("System busy generating item codes. Please retry in a moment."))

    try:
        doc.item_code = _next_item_code(prefix)
    except Exception as e:
        frappe.log_error(f"Error generating item code for prefix {prefix}: {str(e)}", "Item Code Generation Error")
        raise
    finally:
        # Guaranteed cleanup — always release the lock
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name)


def _set_item_name(doc):
    """Set item_name for template items (no spec values — just model/brand)."""
    if not doc.ch_model:
        return

    manufacturer, brand = _get_model_fields(doc)
    generated = generate_item_name(
        sub_category=doc.ch_sub_category,
        manufacturer=manufacturer,
        brand=brand,
        model=doc.ch_model,
        spec_values=[],  # Template has no spec values
    )
    if generated:
        doc.item_name = generated
        # ch_display_name is set by before_save which runs immediately after
        doc.ch_display_name = generated
