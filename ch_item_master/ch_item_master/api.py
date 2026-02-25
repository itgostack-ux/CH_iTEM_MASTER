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

Internal helpers (not whitelisted):
  - _next_item_code         (overrides/item.py)
  - _group_model_spec_values (shared by _get_spec_selectors, _get_property_specs, get_model_attribute_values)
  - _get_spec_selectors     (get_model_details)
  - _get_property_specs     (get_model_details)
"""

import json
from collections import defaultdict

import frappe
from frappe import _


# ───────────────────────────────────────────────────────────────────────────────
# Item Code (internal)
# ───────────────────────────────────────────────────────────────────────────────

def _next_item_code(prefix):
    """Return the next sequential item code for *prefix*.

    Uses LIKE for index-friendliness on large tables.
    Format: <PREFIX><6-digit-seq>
    """
    prefix_len = len(prefix)
    expected_len = prefix_len + 6  # fixed: PREFIX + exactly 6 digits
    result = frappe.db.sql(
        """
        SELECT MAX(CAST(SUBSTRING(item_code, %s) AS UNSIGNED)) AS last_num
        FROM `tabItem`
        WHERE item_code LIKE %s
          AND CHAR_LENGTH(item_code) = %s
        """,
        (prefix_len + 1, f"{prefix}%", expected_len),
        as_dict=True,
    )
    last_num = result[0].last_num or 0 if result else 0
    return f"{prefix}{str(last_num + 1).zfill(6)}"


# ───────────────────────────────────────────────────────────────────────────────
# Spec Selectors (internal — used by get_model_details)
# ───────────────────────────────────────────────────────────────────────────────

def _group_model_spec_values(model):
    """Return CH Model Spec Values grouped by spec name.

    Returns: dict {spec_name: [value1, value2, ...]}
    """
    rows = frappe.get_all(
        "CH Model Spec Value",
        filters={"parent": model, "parenttype": "CH Model"},
        fields=["spec", "spec_value"],
        order_by="idx asc",
    )
    # Use set for O(1) deduplication, then convert to list
    grouped = defaultdict(set)
    for sv in rows:
        if sv.spec and sv.spec_value:
            grouped[sv.spec].add(sv.spec_value)
    return {k: list(v) for k, v in grouped.items()}


def _get_spec_selectors(sub_category, model, grouped=None):
    """Return variant specs (is_variant=1, in_item_name=1) and their allowed values from the model.

    Returns: list of {spec, values: ['Black', 'White', ...]}
    """
    specs_in_name = frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": sub_category, "parenttype": "CH Sub Category",
                 "in_item_name": 1, "is_variant": 1},
        fields=["spec", "name_order"],
        order_by="name_order asc, idx asc",
    )
    if not specs_in_name:
        return []

    if grouped is None:
        grouped = _group_model_spec_values(model)
    return [
        {"spec": row.spec, "values": grouped.get(row.spec, [])}
        for row in specs_in_name
    ]


def _get_property_specs(sub_category, model, grouped=None):
    """Return all specs that do NOT drive variant creation.

    A spec drives variant creation only if is_variant=1 AND in_item_name=1.
    Everything else (is_variant=0 OR in_item_name=0) is an attribute/property
    stored on the item.

    Returns: list of {spec, values: ['Dual SIM', ...]}
    """
    all_specs = frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": sub_category, "parenttype": "CH Sub Category"},
        fields=["spec", "is_variant", "in_item_name"],
        order_by="idx asc",
    )
    # Exclude variant-driving specs (is_variant=1 AND in_item_name=1)
    property_specs = [
        row for row in all_specs
        if not (row.is_variant and row.in_item_name)
    ]
    if not property_specs:
        return []

    if grouped is None:
        grouped = _group_model_spec_values(model)
    return [
        {"spec": row.spec, "values": grouped.get(row.spec, [])}
        for row in property_specs
    ]


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
        mfr_name = (frappe.db.get_value("Manufacturer", manufacturer, "short_name") or "").strip()
        if mfr_name:
            name_parts.append(mfr_name)

    # 2. Brand (deduplicate against parts already added)
    if sub_cat.include_brand_in_name and brand:
        brand_name = (frappe.db.get_value("Brand", brand, "brand") or "").strip()
        lower_parts = {p.lower() for p in name_parts}
        if brand_name and brand_name.lower() not in lower_parts:
            name_parts.append(brand_name)

    # 3. Model
    if sub_cat.include_model_in_name and (model or model_name_override):
        model_name = (model_name_override or
                      (frappe.db.get_value("CH Model", model, "model_name") if model else "") or
                      "").strip()
        if model_name:
            name_parts.append(model_name)

    # 4. Spec values (only variant specs, only when explicitly provided)
    if spec_values:
        specs_in_name = frappe.get_all(
            "CH Sub Category Spec",
            filters={"parent": sub_category, "parenttype": "CH Sub Category",
                     "in_item_name": 1, "is_variant": 1},
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
