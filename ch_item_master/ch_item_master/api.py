# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Item Master — Whitelisted API methods.

Endpoints called from client-side JS:
  - get_model_details              (item.js)
  - generate_item_name             (ch_model.js + overrides/item.py)
  - get_sub_category_manufacturers (ch_model.js)
  - get_attribute_values           (ch_model.js)
  - search_specs_for_sub_category  (ch_model.js + item.js — supports is_variant filter)
  - get_model_attribute_values     (item.js variant dialogs)
  - get_property_spec_values        (item.js ch_spec_values autocomplete)
  - generate_items_from_model      (ch_model.js — bulk variant generation)

Internal helpers (in utils.py — imported here for use):
  - _next_item_code         (overrides/item.py)
  - _group_model_spec_values (shared by _get_spec_selectors, _get_property_specs, get_model_attribute_values)
  - _get_spec_selectors     (get_model_details)
  - _get_property_specs     (get_model_details)
"""

import json
from itertools import product as cartesian_product

import frappe
from frappe import _

from ch_item_master.ch_item_master.utils import (
    _get_property_specs,
    _get_spec_selectors,
    _group_model_spec_values,
    _next_item_code,
)


# ───────────────────────────────────────────────────────────────────────────────
# Item Name
# ───────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def generate_item_name(sub_category, manufacturer=None, brand=None, model=None,
                       spec_values=None, model_name_override=None):
    """Return the auto-generated item name.

    For templates: spec_values=[] -> name without specs.
    For variants:  spec_values=[{spec, spec_value}] -> includes specs.
    model_name_override: raw model name text (preview of unsaved models).
    """
    # Decode JSON early so the rest of the function works with a list
    if isinstance(spec_values, str):
        spec_values = json.loads(spec_values) if spec_values else []
    spec_values = spec_values or []

    sub_cat = frappe.db.get_value(
        "CH Sub Category",
        sub_category,
        ["include_manufacturer_in_name", "include_brand_in_name", "include_model_in_name"],
        as_dict=True,
    )
    if not sub_cat:
        frappe.throw(_("Sub Category {0} not found").format(sub_category))

    name_parts = []

    # 1. Manufacturer
    if sub_cat.include_manufacturer_in_name and manufacturer:
        mfr_name = (
            frappe.db.get_value("Manufacturer", manufacturer, "short_name")
            or manufacturer  # fallback to the Manufacturer ID/name
        ).strip()
        if mfr_name:
            name_parts.append(mfr_name)

    # 2. Brand (deduplicate against parts already added)
    if sub_cat.include_brand_in_name and brand:
        brand_name = (frappe.db.get_value("Brand", brand, "brand") or "").strip()
        lower_parts = {p.lower() for p in name_parts}
        if brand_name and brand_name.lower() not in lower_parts:
            name_parts.append(brand_name)

    # 3. Model (deduplicate: if model name starts with brand/manufacturer already added, skip the prefix)
    if sub_cat.include_model_in_name and (model or model_name_override):
        model_name = (model_name_override or
                      (frappe.db.get_value("CH Model", model, "model_name") if model else "") or
                      "").strip()
        if model_name:
            # If the model name starts with a previously-added part (e.g. brand),
            # drop that part from name_parts to avoid "Galaxy Galaxy S25"
            for i, part in enumerate(list(name_parts)):
                if model_name.lower().startswith(part.lower()):
                    name_parts.pop(i)
                    break
            name_parts.append(model_name)

    # 4. Spec values (only variant specs, only when explicitly provided)
    if spec_values:
        specs_in_name = frappe.get_all(
            "CH Sub Category Spec",
            filters={"parent": sub_category, "parenttype": "CH Sub Category",
                     "in_item_name": 1},
            fields=["spec", "name_order"],
            order_by="name_order asc, idx asc",
        )
        if specs_in_name:
            spec_value_map = {
                sv["spec"]: sv["spec_value"]
                for sv in spec_values
                if sv.get("spec_value")
            }
            for spec_row in specs_in_name:
                val = str(spec_value_map.get(spec_row.spec, "")).strip()
                if val:
                    name_parts.append(val)

    return " ".join(name_parts).strip()


# ───────────────────────────────────────────────────────────────────────────────
# Cascading dropdown helpers
# ───────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_sub_category_manufacturers(sub_category):
    """Return manufacturer names allowed for a given sub-category."""
    return frappe.get_all(
        "CH Sub Category Manufacturer",
        filters={"parent": sub_category, "parenttype": "CH Sub Category"},
        pluck="manufacturer",
    )


# ───────────────────────────────────────────────────────────────────────────────
# Attribute Value autocomplete for CH Model Spec Value child table
# ───────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_attribute_values(spec="", txt="", **kwargs):
    """Return attribute values for a given Item Attribute (spec).

    Used by the Autocomplete get_query on spec_value in CH Model form.
    """
    if not spec:
        return []

    filters = {"parent": spec}
    if txt:
        filters["attribute_value"] = ("like", f"%{txt}%")

    values = frappe.get_all(
        "Item Attribute Value",
        filters=filters,
        fields=["attribute_value"],
        order_by="attribute_value asc",
        limit_page_length=50,
    )
    return [v.attribute_value for v in values]


# ───────────────────────────────────────────────────────────────────────────────
# Link-search for Spec field in CH Model Spec Value child table
# ───────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def search_specs_for_sub_category(doctype, txt, searchfield, start, page_len, filters):
    """Link query for 'spec' field in CH Model Spec Value / CH Item Spec Value.

    Filters:
      sub_category               — required, only return specs mapped to this sub-category
      is_variant                 — optional (0 or 1), filter by variant type
      exclude_variant_selectors  — optional (1), exclude specs that drive variant creation
                                   (is_variant=1 AND in_item_name=1)
    """
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)
    sub_category = (filters or {}).get("sub_category", "")

    # Build conditions list for safer SQL construction
    conditions = []
    values = {"sub_category": sub_category, "start": int(start), "page_len": int(page_len)}
    
    if txt:
        conditions.append("ia.attribute_name LIKE %(txt)s")
        values["txt"] = f"%{txt}%"

    is_variant = (filters or {}).get("is_variant", None)
    if is_variant is not None:
        conditions.append("csp.is_variant = %(is_variant)s")
        values["is_variant"] = int(is_variant)

    # Exclude variant-driving specs (is_variant=1 AND in_item_name=1)
    if (filters or {}).get("exclude_variant_selectors"):
        conditions.append("NOT (csp.is_variant = 1 AND csp.in_item_name = 1)")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    return frappe.db.sql(
        f"""
        SELECT ia.name, ia.attribute_name
        FROM `tabItem Attribute` ia
        INNER JOIN `tabCH Sub Category Spec` csp
            ON csp.spec = ia.name
            AND csp.parent = %(sub_category)s
            AND csp.parenttype = 'CH Sub Category'
        WHERE {where_clause}
        ORDER BY ia.attribute_name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        values,
    )


# ───────────────────────────────────────────────────────────────────────────────
# Model details for Item form auto-fill
# ───────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_model_details(model):
    """Return details needed to auto-fill the Item form from a CH Model.

    Returns: sub_category, category, manufacturer, brand, spec_selectors,
    hsn_code, gst_rate, item_group.
    """
    if not model:
        return {}

    mdoc = frappe.db.get_value(
        "CH Model", model,
        ["sub_category", "manufacturer", "brand"],
        as_dict=True,
    )
    if not mdoc:
        frappe.throw(_("Model {0} not found").format(model))

    sc_data = frappe.db.get_value(
        "CH Sub Category", mdoc.sub_category,
        ["category", "hsn_code", "gst_rate"],
        as_dict=True,
    ) or {}

    category = sc_data.get("category", "")
    item_group = frappe.db.get_value("CH Category", category, "item_group") if category else ""

    # Compute spec values once — shared by both _get_spec_selectors and _get_property_specs
    grouped_specs = _group_model_spec_values(model)
    spec_selectors = _get_spec_selectors(mdoc.sub_category, model, grouped=grouped_specs)
    # has_variants is derived: true only if there are variant-driving specs
    has_variants = len(spec_selectors) > 0

    return {
        "sub_category": mdoc.sub_category,
        "category": category,
        "manufacturer": mdoc.manufacturer,
        "brand": mdoc.brand,
        "has_variants": has_variants,
        "spec_selectors": spec_selectors,
        "property_specs": _get_property_specs(mdoc.sub_category, model, grouped=grouped_specs),
        "hsn_code": sc_data.get("hsn_code", ""),
        "gst_rate": sc_data.get("gst_rate", 0),
        "item_group": item_group or "",
    }


@frappe.whitelist()
def get_model_attribute_values(model):
    """Return all spec values mapped to a CH Model, grouped by attribute.

    Used by the variant creation dialog to show only model-allowed values
    instead of all Item Attribute Values.

    Returns: dict {attribute_name: [value1, value2, ...], ...}
    """
    if not model:
        return {}

    sub_category = frappe.db.get_value("CH Model", model, "sub_category")
    if not sub_category:
        return {}

    variant_spec_set = set(frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": sub_category, "parenttype": "CH Sub Category", "is_variant": 1},
        pluck="spec",
    ))

    grouped = _group_model_spec_values(model)
    return {spec: vals for spec, vals in grouped.items() if spec in variant_spec_set}


@frappe.whitelist()
def get_property_spec_values(model, spec):
    """Return the values mapped for a single spec on a CH Model.

    Used by the ch_spec_values child table to offer autocomplete options
    restricted to model-mapped values for the selected spec.

    Returns: list of value strings, e.g. ['Dual SIM', 'Single SIM']
    """
    if not model or not spec:
        return []

    grouped = _group_model_spec_values(model)
    return grouped.get(spec, [])


# ───────────────────────────────────────────────────────────────────────────────
# Bulk Item Generation from Model
# ───────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def generate_items_from_model(model):
    """Generate all missing ERPNext Item variants for a CH Model.


    Steps:
      1. Ensure the model is active and has variant specs with values
      2. Find or create the template Item
      3. Compute cartesian product of variant-spec values
      4. Create missing variants via ERPNext's create_variant()
      5. Return summary of created / skipped

    Uses ERPNext's native variant system so all standard variant
    features (BOM copy, pricing rules, etc.) work out of the box.
    """
    frappe.only_for(["System Manager", "CH Master Manager"])

    from erpnext.controllers.item_variant import create_variant, get_variant

    mdoc = frappe.get_doc("CH Model", model)
    if mdoc.disabled:
        frappe.throw(_("Model {0} is disabled. Enable it first.").format(
            frappe.bold(mdoc.model_name)), title=_("Disabled Model"))

    # ── 1. Gather variant specs and their values ────────────────────────────
    grouped = _group_model_spec_values(model)
    spec_selectors = _get_spec_selectors(mdoc.sub_category, model, grouped=grouped)

    if not spec_selectors:
        frappe.throw(
            _("No variant specifications found for this model's sub-category. "
              "Items without variant specs should be created directly from the Item list."),
            title=_("No Variant Specs"))

    # Check every variant spec has at least one value
    empty_specs = [s["spec"] for s in spec_selectors if not s["values"]]
    if empty_specs:
        frappe.throw(
            _("These variant specs have no values defined in the model: {0}. "
              "Add values in the Spec Values table first.").format(
                ", ".join(frappe.bold(s) for s in empty_specs)),
            title=_("Missing Spec Values"))

    # ── 2. Find or create the template item ─────────────────────────────────
    template_code = frappe.db.get_value(
        "Item",
        {"ch_model": model, "has_variants": 1},
        "item_code",
    )

    if not template_code:
        # Create template item
        sc_data = frappe.db.get_value(
            "CH Sub Category", mdoc.sub_category,
            ["category", "hsn_code", "gst_rate"],
            as_dict=True,
        ) or {}
        category = sc_data.get("category", "")
        item_group = frappe.db.get_value("CH Category", category, "item_group") if category else ""

        template = frappe.new_doc("Item")
        template.ch_model = model
        template.ch_sub_category = mdoc.sub_category
        template.ch_category = category
        template.item_group = item_group or "All Item Groups"
        template.brand = mdoc.brand
        template.stock_uom = "Nos"
        template.has_variants = 1
        template.variant_based_on = "Item Attribute"

        if sc_data.get("hsn_code"):
            template.gst_hsn_code = sc_data["hsn_code"]

        # Set variant specs as Item Attributes
        for sel in spec_selectors:
            template.append("attributes", {"attribute": sel["spec"]})

        # Set property specs in ch_spec_values
        property_specs = _get_property_specs(mdoc.sub_category, model, grouped=grouped)
        for ps in property_specs:
            for val in ps["values"]:
                template.append("ch_spec_values", {"spec": ps["spec"], "spec_value": val})

        template.insert(ignore_permissions=True)
        template_code = template.item_code
        frappe.db.commit()

    # ── 3. Compute cartesian product ────────────────────────────────────────
    # spec_selectors = [{"spec": "Color", "values": ["Black", "White"]},
    #                   {"spec": "Storage", "values": ["128GB", "256GB"]}]
    spec_names = [s["spec"] for s in spec_selectors]
    value_lists = [s["values"] for s in spec_selectors]

    total_combos = 1
    for vl in value_lists:
        total_combos *= len(vl)

    if total_combos > 500:
        frappe.throw(
            _("Too many combinations ({0}). Maximum is 500. "
              "Reduce variation values or create in batches.").format(total_combos),
            title=_("Too Many Variants"))

    created = 0
    skipped = 0
    errors = []

    for combo in cartesian_product(*value_lists):
        args = dict(zip(spec_names, combo))

        # Check if variant already exists
        existing = get_variant(template_code, args=args)
        if existing:
            skipped += 1
            continue

        try:
            variant = create_variant(template_code, args)
            variant.insert(ignore_permissions=True)
            created += 1
        except Exception as e:
            errors.append(f"{args}: {str(e)}")
            frappe.log_error(
                f"Error creating variant for model {model}: {args}\n{str(e)}",
                "Bulk Item Generation Error",
            )

    frappe.db.commit()

    return {
        "template": template_code,
        "total_combinations": total_combos,
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


# ───────────────────────────────────────────────────────────────────────────────
# Model search for Item form — shows brand + manufacturer as description
# ───────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def search_models(doctype, txt, searchfield, start, page_len, filters):
    """Custom link search for CH Model that shows Brand and Manufacturer.

    Returns results like:
        name | model_name | Brand: Samsung | Manufacturer: Samsung Electronics

    This makes it easy to distinguish same-named models under different brands.
    """
    conditions = []
    values = {}

    if isinstance(filters, str):
        filters = json.loads(filters)

    if filters.get("disabled") is not None:
        if not filters["disabled"]:
            conditions.append("m.disabled = 0")
        else:
            conditions.append("m.disabled = 1")
    if filters.get("sub_category"):
        conditions.append("m.sub_category = %(sub_category)s")
        values["sub_category"] = filters["sub_category"]

    # Text search across model_name, brand, manufacturer
    if txt:
        conditions.append(
            "(m.model_name LIKE %(txt)s OR m.brand LIKE %(txt)s "
            "OR m.manufacturer LIKE %(txt)s OR m.name LIKE %(txt)s)"
        )
        values["txt"] = f"%{txt}%"

    where = " AND ".join(conditions) if conditions else "1=1"

    return frappe.db.sql(
        f"""
        SELECT m.name, m.model_name,
               CONCAT('Brand: ', m.brand, ' | Mfr: ', m.manufacturer) as description
        FROM `tabCH Model` m
        WHERE {where}
        ORDER BY m.model_name ASC
        LIMIT %(page_len)s OFFSET %(start)s
        """,
        {**values, "start": int(start), "page_len": int(page_len)},
    )
