# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Model Coverage — Script Report

Shows all active CH Models with:
  - How many variant spec combinations are possible
  - How many items actually exist (template + variants)
  - Coverage % (items created / expected)
  - Whether a template item exists

Helps identify models that need item generation.
"""

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "model", "label": _("Model"), "fieldtype": "Link",
         "options": "CH Model", "width": 150},
        {"fieldname": "model_name", "label": _("Model Name"), "fieldtype": "Data", "width": 180},
        {"fieldname": "sub_category", "label": _("Sub Category"), "fieldtype": "Link",
         "options": "CH Sub Category", "width": 140},
        {"fieldname": "manufacturer", "label": _("Manufacturer"), "fieldtype": "Link",
         "options": "Manufacturer", "width": 120},
        {"fieldname": "brand", "label": _("Brand"), "fieldtype": "Link",
         "options": "Brand", "width": 100},
        {"fieldname": "variant_specs", "label": _("Variant Specs"), "fieldtype": "Int", "width": 100},
        {"fieldname": "expected_items", "label": _("Expected Items"), "fieldtype": "Int", "width": 120},
        {"fieldname": "actual_items", "label": _("Actual Items"), "fieldtype": "Int", "width": 110},
        {"fieldname": "has_template", "label": _("Template?"), "fieldtype": "Data", "width": 90},
        {"fieldname": "coverage_pct", "label": _("Coverage %"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "disabled", "label": _("Disabled"), "fieldtype": "Check", "width": 70},
    ]


def get_data(filters):
    conditions = []
    values = {}

    if filters.get("sub_category"):
        conditions.append("m.sub_category = %(sub_category)s")
        values["sub_category"] = filters["sub_category"]

    if filters.get("category"):
        conditions.append("m.sub_category IN (SELECT name FROM `tabCH Sub Category` WHERE category = %(category)s)")
        values["category"] = filters["category"]

    show_inactive = filters.get("show_inactive")
    if not show_inactive:
        conditions.append("m.disabled = 0")

    where = " AND ".join(conditions) if conditions else "1=1"

    models = frappe.db.sql("""
        SELECT
            m.name as model,
            m.model_name,
            m.sub_category,
            m.manufacturer,
            m.brand,
            m.disabled
        FROM `tabCH Model` m
        WHERE {where}
        ORDER BY m.sub_category, m.model_name
    """.format(where=where), values, as_dict=True)

    for row in models:
        # Count variant specs and expected combinations
        variant_specs = _get_variant_spec_info(row.model, row.sub_category)
        row["variant_specs"] = variant_specs["count"]
        row["expected_items"] = variant_specs["combinations"]

        # Count actual items
        row["actual_items"] = frappe.db.count("Item", {
            "ch_model": row.model,
            "has_variants": 0,  # Only variant items, not template
        })

        # Check template
        template = frappe.db.get_value("Item", {
            "ch_model": row.model, "has_variants": 1
        }, "item_code")
        row["has_template"] = "Yes" if template else "No"

        # Coverage
        if variant_specs["combinations"] > 0:
            row["coverage_pct"] = round(
                (row["actual_items"] / variant_specs["combinations"]) * 100, 1
            )
        else:
            row["coverage_pct"] = 100.0 if row["actual_items"] > 0 else 0.0

    return models


def _get_variant_spec_info(model, sub_category):
    """Return count of variant specs and total expected combinations."""
    from collections import defaultdict

    # Get variant specs for this sub-category
    variant_specs = frappe.get_all(
        "CH Sub Category Spec",
        filters={
            "parent": sub_category,
            "parenttype": "CH Sub Category",
            "is_variant": 1,
            "in_item_name": 1,
        },
        pluck="spec",
    )
    if not variant_specs:
        return {"count": 0, "combinations": 0}

    # Get model's values per variant spec
    spec_values = frappe.get_all(
        "CH Model Spec Value",
        filters={"parent": model, "parenttype": "CH Model"},
        fields=["spec", "spec_value"],
    )

    grouped = defaultdict(set)
    for sv in spec_values:
        if sv.spec in variant_specs and sv.spec_value:
            grouped[sv.spec].add(sv.spec_value)

    combinations = 1
    for spec in variant_specs:
        val_count = len(grouped.get(spec, set()))
        if val_count > 0:
            combinations *= val_count
        else:
            combinations = 0
            break

    return {"count": len(variant_specs), "combinations": combinations}
