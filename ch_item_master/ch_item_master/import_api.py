# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Item Master — Bulk Import API

Two entry points:
  import_masters(data)        — structured JSON payload
  import_masters_from_csv()   — CSV file upload (attached to request)

Behaviour:
  1. Normalise all input text (trim, collapse multiple spaces).
  2. Validate ALL external references up-front (fail-fast):
       • Manufacturer, Brand, Item Attribute, GST HSN Code, Item Group
         must already exist.
       • Item Attribute Values are auto-created if the parent attribute exists.
  3. If any validation error → return ALL errors, create nothing.
  4. On success → create Category → Sub Category → Model (top-down).
  5. Records that already exist are skipped (idempotent).
  6. Returns a summary with created / skipped / error counts.

JSON payload shape:
  {
    "categories": [
      {
        "category_name": "Mobiles",
        "item_group": "Products",
        "sub_categories": [
          {
            "sub_category_name": "Smart Phones",
            "hsn_code": "85171300",
            "gst_rate": 18,
            "prefix": "SP",
            "include_manufacturer_in_name": 1,
            "include_brand_in_name": 0,
            "include_model_in_name": 1,
            "manufacturers": ["Apple", "Samsung"],
            "specs": [
              {"spec": "Color", "is_variant": 1, "in_item_name": 1, "name_order": 1},
              {"spec": "Storage", "is_variant": 1, "in_item_name": 1, "name_order": 2}
            ],
            "models": [
              {
                "model_name": "iPhone 15",
                "manufacturer": "Apple",
                "brand": "Apple",
                "spec_values": [
                  {"spec": "Color", "value": "Black"},
                  {"spec": "Color", "value": "White"},
                  {"spec": "Storage", "value": "128GB"},
                  {"spec": "Storage", "value": "256GB"}
                ]
              }
            ]
          }
        ]
      }
    ]
  }

CSV flat format (one row per model-spec-value):
  category, item_group, sub_category_name, hsn_code, gst_rate, prefix,
  include_manufacturer_in_name, include_brand_in_name, include_model_in_name,
  manufacturer, brand, model_name, spec, spec_value, is_variant,
  in_item_name, name_order
"""

import csv
import io
import json
from collections import OrderedDict, defaultdict

import frappe
from frappe import _
from frappe.utils import escape_html


# ─────────────────────────────────────────────────────────────────────────────
# Text normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _norm(text):
    """Normalise for display: trim and collapse multiple spaces."""
    if not text:
        return ""
    return " ".join(str(text).strip().split())


def _norm_key(text):
    """Normalise for matching / lookup: trim, collapse spaces, upper-case."""
    return _norm(text).upper()


# ─────────────────────────────────────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_lookup(doctype, name_field="name"):
    """Build a {NORMALISED_NAME: actual_name} dict for a doctype.

    Used for case-insensitive / whitespace-tolerant matching.
    """
    rows = frappe.get_all(doctype, pluck=name_field)
    return {_norm_key(r): r for r in rows}


def _resolve(lookup, raw_value, doctype_label, path, errors):
    """Try to find *raw_value* in *lookup*.  On miss, append to *errors*.

    Returns the canonical name or None.
    """
    if not raw_value:
        errors.append({"path": path, "error": f"{doctype_label} is blank"})
        return None
    key = _norm_key(raw_value)
    actual = lookup.get(key)
    if not actual:
        errors.append({
            "path": path,
            "error": f'{doctype_label} "{_norm(raw_value)}" not found',
        })
    return actual


def _ensure_attribute_value(attribute, value):
	"""Create an Item Attribute Value if it doesn't exist yet.

	Returns the canonical value string.

	Fetches all existing values in a single query and checks for an exact
	match first, then falls back to a case-insensitive match — avoids the
	previous two-query pattern (exact query + full fetch).
	"""
	value = _norm(value)
	if not value:
		return ""

	# Sanitize input to prevent XSS
	value = escape_html(value)

	# Single query: fetch all existing values for this attribute
	all_vals = frappe.get_all(
		"Item Attribute Value",
		filters={"parent": attribute},
		pluck="attribute_value",
	)

	if value in all_vals:
		return value  # exact match

	# Case-insensitive match
	val_map = {v.upper(): v for v in all_vals}
	if value.upper() in val_map:
		return val_map[value.upper()]

	# Auto-create with proper permissions
	attr_doc = frappe.get_doc("Item Attribute", attribute)
	attr_doc.append("item_attribute_values", {
		"attribute_value": value,
		"abbr": value[:3].upper(),
	})
	attr_doc.flags.ignore_permissions = True
	attr_doc.save()
	return value


# ─────────────────────────────────────────────────────────────────────────────
# Core import logic
# ─────────────────────────────────────────────────────────────────────────────

def _validate_and_import(payload):
    """Validate all references, then create masters top-down.

    Returns: {"success": bool, "summary": {...}, "errors": [...]}
    """
    errors = []
    summary = {
        "categories": {"created": 0, "skipped": 0},
        "sub_categories": {"created": 0, "skipped": 0},
        "models": {"created": 0, "skipped": 0},
        "attribute_values": {"created": 0},
    }

    categories = payload.get("categories") or []
    if not categories:
        return {"success": False, "summary": summary,
                "errors": [{"path": "", "error": "No categories in payload"}]}

    # ── Build lookups for external references ────────────────────────────
    mfr_lookup = _build_lookup("Manufacturer")
    brand_lookup = _build_lookup("Brand")
    attr_lookup = _build_lookup("Item Attribute")
    ig_lookup = _build_lookup("Item Group")
    hsn_lookup = _build_lookup("GST HSN Code")
    cat_lookup = _build_lookup("CH Category")
    # Sub-category lookup: key = "CATEGORY-SUBCATEGORY"
    sc_rows = frappe.get_all(
        "CH Sub Category",
        fields=["name", "category", "sub_category_name"],
    )
    sc_lookup = {
        _norm_key(f"{r.category}-{r.sub_category_name}"): r.name
        for r in sc_rows
    }
    model_lookup = _build_lookup("CH Model", "model_name")
    # Model names are now '{sub_category}-{brand}-{model_name}'
    # Use name-based lookup for de-duplication
    model_name_lookup = _build_lookup("CH Model")

    # ── Phase 1: Validate everything ─────────────────────────────────────
    # We collect all errors before creating anything.
    validated = []  # list of clean, resolved records to create

    for cat_idx, cat in enumerate(categories):
        cat_name = _norm(cat.get("category_name", ""))
        cat_path = cat_name or f"categories[{cat_idx}]"

        if not cat_name:
            errors.append({"path": cat_path, "error": "category_name is blank"})
            continue

        item_group = _resolve(
            ig_lookup, cat.get("item_group"), "Item Group", cat_path, errors
        )

        sub_categories = cat.get("sub_categories") or []
        validated_scs = []

        for sc_idx, sc in enumerate(sub_categories):
            sc_name = _norm(sc.get("sub_category_name", ""))
            sc_path = f"{cat_path} > {sc_name or f'sub_categories[{sc_idx}]'}"

            if not sc_name:
                errors.append({"path": sc_path, "error": "sub_category_name is blank"})
                continue

            prefix = _norm(sc.get("prefix", "")).upper()
            if not prefix:
                errors.append({"path": sc_path, "error": "prefix is blank"})

            # HSN code (optional but validated if provided)
            hsn_raw = _norm(sc.get("hsn_code", ""))
            hsn_code = None
            if hsn_raw:
                hsn_code = _resolve(hsn_lookup, hsn_raw, "GST HSN Code", sc_path, errors)

            # Validate manufacturers list
            sc_manufacturers = sc.get("manufacturers") or []
            resolved_mfrs = []
            for mfr_raw in sc_manufacturers:
                mfr = _resolve(mfr_lookup, mfr_raw, "Manufacturer", sc_path, errors)
                if mfr:
                    resolved_mfrs.append(mfr)

            # Validate specs
            sc_specs = sc.get("specs") or []
            resolved_specs = []
            for spec_def in sc_specs:
                spec_name = _resolve(
                    attr_lookup, spec_def.get("spec"), "Item Attribute",
                    f"{sc_path} > specs", errors
                )
                if spec_name:
                    resolved_specs.append({
                        "spec": spec_name,
                        "is_variant": int(spec_def.get("is_variant", 1)),
                        "in_item_name": int(spec_def.get("in_item_name", 0)),
                        "name_order": int(spec_def.get("name_order", 0)),
                    })

            # Validate models
            models = sc.get("models") or []
            validated_models = []

            for mdl_idx, mdl in enumerate(models):
                mdl_name = _norm(mdl.get("model_name", ""))
                mdl_path = f"{sc_path} > {mdl_name or f'models[{mdl_idx}]'}"

                if not mdl_name:
                    errors.append({"path": mdl_path, "error": "model_name is blank"})
                    continue

                mdl_mfr = _resolve(
                    mfr_lookup, mdl.get("manufacturer"), "Manufacturer", mdl_path, errors
                )
                mdl_brand = _resolve(
                    brand_lookup, mdl.get("brand"), "Brand", mdl_path, errors
                )

                # Validate model's manufacturer is in sub-category's allowed list
                if mdl_mfr and resolved_mfrs and mdl_mfr not in resolved_mfrs:
                    errors.append({
                        "path": mdl_path,
                        "error": f'Manufacturer "{mdl_mfr}" not in sub-category allowed list: {resolved_mfrs}',
                    })

                # Validate spec values
                spec_values = mdl.get("spec_values") or []
                resolved_svs = []
                sc_spec_names = {s["spec"] for s in resolved_specs}

                for sv in spec_values:
                    sv_spec = _resolve(
                        attr_lookup, sv.get("spec"), "Item Attribute",
                        f"{mdl_path} > spec_values", errors
                    )
                    sv_value = _norm(sv.get("value", ""))
                    if sv_spec and not sv_value:
                        errors.append({
                            "path": f"{mdl_path} > {sv_spec}",
                            "error": "spec value is blank",
                        })
                    if sv_spec and sv_value:
                        # Validate spec belongs to sub-category
                        if sc_spec_names and sv_spec not in sc_spec_names:
                            errors.append({
                                "path": f"{mdl_path} > {sv_spec}",
                                "error": f'Spec "{sv_spec}" not defined in sub-category specs',
                            })
                        resolved_svs.append({"spec": sv_spec, "value": sv_value})

                validated_models.append({
                    "model_name": mdl_name,
                    "manufacturer": mdl_mfr,
                    "brand": mdl_brand,
                    "is_active": int(mdl.get("is_active", 1)),
                    "spec_values": resolved_svs,
                })

            validated_scs.append({
                "sub_category_name": sc_name,
                "prefix": prefix,
                "hsn_code": hsn_code,
                "gst_rate": float(sc.get("gst_rate", 0)),
                "include_manufacturer_in_name": int(sc.get("include_manufacturer_in_name", 1)),
                "include_brand_in_name": int(sc.get("include_brand_in_name", 0)),
                "include_model_in_name": int(sc.get("include_model_in_name", 1)),
                "manufacturers": resolved_mfrs,
                "specs": resolved_specs,
                "models": validated_models,
            })

        validated.append({
            "category_name": cat_name,
            "item_group": item_group,
            "sub_categories": validated_scs,
        })

    # ── Fail-fast: return ALL errors without creating anything ────────────
    if errors:
        return {"success": False, "summary": summary, "errors": errors}

    # ── Phase 2: Create masters top-down ─────────────────────────────────
    for cat_data in validated:
        cat_name = cat_data["category_name"]

        # Create or skip CH Category
        if _norm_key(cat_name) in cat_lookup:
            summary["categories"]["skipped"] += 1
            cat_doc_name = cat_lookup[_norm_key(cat_name)]
        else:
            cat_doc = frappe.new_doc("CH Category")
            cat_doc.category_name = cat_name
            cat_doc.item_group = cat_data["item_group"]
            cat_doc.is_active = 1
            cat_doc.insert(ignore_permissions=True)
            cat_doc_name = cat_doc.name
            cat_lookup[_norm_key(cat_name)] = cat_doc_name
            summary["categories"]["created"] += 1

        for sc_data in cat_data["sub_categories"]:
            sc_key = _norm_key(f"{cat_doc_name}-{sc_data['sub_category_name']}")

            if sc_key in sc_lookup:
                summary["sub_categories"]["skipped"] += 1
                sc_doc_name = sc_lookup[sc_key]
            else:
                sc_doc = frappe.new_doc("CH Sub Category")
                sc_doc.category = cat_doc_name
                sc_doc.sub_category_name = sc_data["sub_category_name"]
                sc_doc.prefix = sc_data["prefix"]
                sc_doc.hsn_code = sc_data["hsn_code"] or ""
                sc_doc.gst_rate = sc_data["gst_rate"]
                sc_doc.include_manufacturer_in_name = sc_data["include_manufacturer_in_name"]
                sc_doc.include_brand_in_name = sc_data["include_brand_in_name"]
                sc_doc.include_model_in_name = sc_data["include_model_in_name"]

                for mfr in sc_data["manufacturers"]:
                    sc_doc.append("manufacturers", {"manufacturer": mfr})

                for spec in sc_data["specs"]:
                    sc_doc.append("specifications", spec)

                sc_doc.insert(ignore_permissions=True)
                sc_doc_name = sc_doc.name
                sc_lookup[sc_key] = sc_doc_name
                summary["sub_categories"]["created"] += 1

            for mdl_data in sc_data["models"]:
                # Composite key matches new autoname: {sub_category}-{brand}-{model_name}
                mdl_key = _norm_key(f"{sc_doc_name}-{mdl_data['brand']}-{mdl_data['model_name']}")

                if mdl_key in model_name_lookup:
                    summary["models"]["skipped"] += 1
                else:
                    # Ensure attribute values exist before creating model
                    for sv in mdl_data["spec_values"]:
                        before_count = frappe.db.count(
                            "Item Attribute Value",
                            {"parent": sv["spec"]},
                        )
                        _ensure_attribute_value(sv["spec"], sv["value"])
                        after_count = frappe.db.count(
                            "Item Attribute Value",
                            {"parent": sv["spec"]},
                        )
                        if after_count > before_count:
                            summary["attribute_values"]["created"] += 1

                    mdl_doc = frappe.new_doc("CH Model")
                    mdl_doc.sub_category = sc_doc_name
                    mdl_doc.model_name = mdl_data["model_name"]
                    mdl_doc.manufacturer = mdl_data["manufacturer"]
                    mdl_doc.brand = mdl_data["brand"]
                    mdl_doc.is_active = mdl_data["is_active"]

                    for sv in mdl_data["spec_values"]:
                        mdl_doc.append("spec_values", {
                            "spec": sv["spec"],
                            "spec_value": sv["value"],
                        })

                    mdl_doc.insert(ignore_permissions=True)
                    model_name_lookup[mdl_key] = mdl_doc.name
                    summary["models"]["created"] += 1

    # Remove manual commit - let Frappe handle transaction boundaries
    # frappe.db.commit() removed for proper atomicity

    return {"success": True, "summary": summary, "errors": []}


# ─────────────────────────────────────────────────────────────────────────────
# CSV → structured JSON converter
# ─────────────────────────────────────────────────────────────────────────────

_CSV_COLUMNS = [
    "category", "item_group", "sub_category_name", "hsn_code", "gst_rate",
    "prefix", "include_manufacturer_in_name", "include_brand_in_name",
    "include_model_in_name", "manufacturer", "brand", "model_name",
    "spec", "spec_value", "is_variant", "in_item_name", "name_order",
]


def _csv_to_payload(rows):
    """Convert flat CSV rows (list of dicts) into the hierarchical JSON
    structure expected by _validate_and_import().

    Grouping order: category → sub_category → model → spec_values.
    """
    # Ordered dicts to preserve insertion order
    categories = OrderedDict()
    
    # Optimization: Cache normalized keys to avoid repeated computation
    _key_cache = {}
    def get_cached_norm_key(val):
        if val not in _key_cache:
            _key_cache[val] = _norm_key(val)
        return _key_cache[val]

    for row_idx, row in enumerate(rows, start=2):  # row 1 = header
        cat_name = _norm(row.get("category", ""))
        if not cat_name:
            continue

        item_group = _norm(row.get("item_group", ""))
        sc_name = _norm(row.get("sub_category_name", ""))
        mdl_name = _norm(row.get("model_name", ""))

        # ─ Category ──────────────────────────────────────────────────────
        cat_key = get_cached_norm_key(cat_name)
        if cat_key not in categories:
            categories[cat_key] = {
                "category_name": cat_name,
                "item_group": item_group,
                "sub_categories": OrderedDict(),
            }

        cat = categories[cat_key]

        if not sc_name:
            continue

        # ─ Sub Category ──────────────────────────────────────────────────
        sc_key = get_cached_norm_key(f"{cat_name}-{sc_name}")
        if sc_key not in cat["sub_categories"]:
            cat["sub_categories"][sc_key] = {
                "sub_category_name": sc_name,
                "hsn_code": _norm(row.get("hsn_code", "")),
                "gst_rate": _to_float(row.get("gst_rate", 0)),
                "prefix": _norm(row.get("prefix", "")).upper(),
                "include_manufacturer_in_name": _to_int(row.get("include_manufacturer_in_name", 1)),
                "include_brand_in_name": _to_int(row.get("include_brand_in_name", 0)),
                "include_model_in_name": _to_int(row.get("include_model_in_name", 1)),
                "manufacturers": OrderedDict(),
                "specs": OrderedDict(),
                "models": OrderedDict(),
            }

        sc = cat["sub_categories"][sc_key]

        # Collect manufacturer for sub-category allowed list
        mfr = _norm(row.get("manufacturer", ""))
        if mfr:
            sc["manufacturers"][get_cached_norm_key(mfr)] = mfr

        # Collect spec for sub-category spec list
        spec = _norm(row.get("spec", ""))
        if spec:
            spec_key = get_cached_norm_key(spec)
            if spec_key not in sc["specs"]:
                sc["specs"][spec_key] = {
                    "spec": spec,
                    "is_variant": _to_int(row.get("is_variant", 1)),
                    "in_item_name": _to_int(row.get("in_item_name", 0)),
                    "name_order": _to_int(row.get("name_order", 0)),
                }

        if not mdl_name:
            continue

        # ─ Model ─────────────────────────────────────────────────────────
        mdl_key = get_cached_norm_key(mdl_name)
        if mdl_key not in sc["models"]:
            sc["models"][mdl_key] = {
                "model_name": mdl_name,
                "manufacturer": mfr,
                "brand": _norm(row.get("brand", "")),
                "spec_values": [],
            }

        mdl = sc["models"][mdl_key]

        # Collect spec value
        spec_value = _norm(row.get("spec_value", ""))
        if spec and spec_value:
            # Deduplicate with cached keys
            existing = {(get_cached_norm_key(sv["spec"]), get_cached_norm_key(sv["value"])) for sv in mdl["spec_values"]}
            if (get_cached_norm_key(spec), get_cached_norm_key(spec_value)) not in existing:
                mdl["spec_values"].append({"spec": spec, "value": spec_value})

    # ── Flatten OrderedDicts into lists ───────────────────────────────────
    result = {"categories": []}
    for cat in categories.values():
        cat_out = {
            "category_name": cat["category_name"],
            "item_group": cat["item_group"],
            "sub_categories": [],
        }
        for sc in cat["sub_categories"].values():
            sc_out = {
                "sub_category_name": sc["sub_category_name"],
                "hsn_code": sc["hsn_code"],
                "gst_rate": sc["gst_rate"],
                "prefix": sc["prefix"],
                "include_manufacturer_in_name": sc["include_manufacturer_in_name"],
                "include_brand_in_name": sc["include_brand_in_name"],
                "include_model_in_name": sc["include_model_in_name"],
                "manufacturers": list(sc["manufacturers"].values()),
                "specs": list(sc["specs"].values()),
                "models": list(sc["models"].values()),
            }
            cat_out["sub_categories"].append(sc_out)
        result["categories"].append(cat_out)

    return result


def _to_int(val):
    """Safe int conversion."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _to_float(val):
    """Safe float conversion."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Whitelisted entry points
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def import_masters(data):
    """Import CH masters from a structured JSON payload.

    Args:
        data: JSON string or dict with the hierarchical payload.

    Returns:
        dict with success, summary, and errors.
    """
    frappe.only_for(["System Manager", "CH Master Manager"])

    if isinstance(data, str):
        data = json.loads(data)

    return _validate_and_import(data)


@frappe.whitelist()
def import_masters_from_csv():
    """Import CH masters from an uploaded CSV file.

    The CSV file must be attached to the HTTP request as 'file'.
    Columns (in order): category, item_group, sub_category_name, hsn_code,
        gst_rate, prefix, include_manufacturer_in_name, include_brand_in_name,
        include_model_in_name, manufacturer, brand, model_name, spec,
        spec_value, is_variant, in_item_name, name_order

    Returns:
        dict with success, summary, and errors.
    """
    frappe.only_for(["System Manager", "CH Master Manager"])

    uploaded = frappe.request.files.get("file")
    if not uploaded:
        frappe.throw(_("No file attached. Please upload a CSV file."))

    # Read file content, handle BOM
    content = uploaded.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    # Normalise column headers
    if reader.fieldnames:
        reader.fieldnames = [_norm(h).lower().replace(" ", "_") for h in reader.fieldnames]

    rows = list(reader)
    if not rows:
        return {"success": False, "summary": {}, "errors": [{"path": "", "error": "CSV file is empty"}]}

    payload = _csv_to_payload(rows)
    return _validate_and_import(payload)


@frappe.whitelist()
def get_import_csv_template():
    """Return the CSV column headers as a downloadable template.

    Returns: dict with headers list and sample_row.
    """
    return {
        "headers": _CSV_COLUMNS,
        "sample_row": {
            "category": "Mobiles",
            "item_group": "Products",
            "sub_category_name": "Smart Phones",
            "hsn_code": "85171300",
            "gst_rate": "18",
            "prefix": "SP",
            "include_manufacturer_in_name": "1",
            "include_brand_in_name": "0",
            "include_model_in_name": "1",
            "manufacturer": "Apple",
            "brand": "Apple",
            "model_name": "iPhone 15",
            "spec": "Color",
            "spec_value": "Black",
            "is_variant": "1",
            "in_item_name": "1",
            "name_order": "1",
        },
    }
