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

from collections import defaultdict

import frappe
from frappe import _

from ch_item_master.config import get_bounded_rows, get_int_setting, require_role_setting


def execute(filters=None):
    frappe.has_permission("CH Model", "read", throw=True)
    frappe.has_permission("Item", "read", throw=True)
    filters = filters or {}
    columns = get_columns()
    data, truncated = get_data(filters)
    return columns, data, _truncated_message(truncated)



def _truncated_message(truncated):
    """Say the list was cut instead of refusing to draw it.

    This report used to `frappe.throw` as soon as the catalogue passed its row
    limit, so the biggest categories -- the ones most worth looking at -- were
    the ones that produced nothing. The bound stays; the reader is now told it
    applied, and the rows are ordered by sub-category then model name, so the kept page is coherent
    rather than an arbitrary slice.
    """
    if not truncated:
        return None
    limit = min(get_int_setting("interactive_report_row_limit", 2000, minimum=1), 10000)
    return _(
        "Showing the first {0} models, by sub-category then model name — there are more. "
        "Narrow the filters to see the rest."
    ).format(limit)

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

    row_limit = min(get_int_setting("interactive_report_row_limit", 2000, minimum=1), 10000)
    values["row_limit"] = row_limit + 1
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
        LIMIT %(row_limit)s
    """.format(where=where), values, as_dict=True)  # noqa: UP032

    truncated = len(models) > row_limit
    if truncated:
        # Trimmed and reported, not refused: a catalogue larger than the
        # bound used to yield no report at all.
        models = models[:row_limit]

    model_names = tuple(row.model for row in models)
    sub_categories = tuple({row.sub_category for row in models if row.sub_category})
    related_limit = min(
        get_int_setting("interactive_report_related_row_limit", 10000, minimum=1),
        50000,
    )

    variant_specs_by_sub_category = defaultdict(list)
    if sub_categories:
        variant_spec_rows = get_bounded_rows(
            "CH Sub Category Spec",
            filters={
                "parent": ("in", sub_categories),
                "parenttype": "CH Sub Category",
                "is_variant": 1,
                "in_item_name": 1,
            },
            fields=["parent", "spec"],
            order_by="parent asc, idx asc",
            limit=related_limit,
        )
        for spec_row in variant_spec_rows:
            variant_specs_by_sub_category[spec_row.parent].append(spec_row.spec)

    spec_values_by_model = defaultdict(lambda: defaultdict(set))
    spec_value_rows = get_bounded_rows(
        "CH Model Spec Value",
        filters={"parent": ("in", model_names), "parenttype": "CH Model"},
        fields=["parent", "spec", "spec_value"],
        limit=related_limit,
    )
    for spec_value in spec_value_rows:
        if spec_value.spec and spec_value.spec_value:
            spec_values_by_model[spec_value.parent][spec_value.spec].add(spec_value.spec_value)

    item_counts = frappe.db.sql(
        """
        SELECT
            ch_model AS model,
            SUM(CASE WHEN has_variants = 0 THEN 1 ELSE 0 END) AS actual_items,
            MAX(CASE WHEN has_variants = 1 THEN 1 ELSE 0 END) AS has_template
        FROM `tabItem`
        WHERE ch_model IN %(models)s
        GROUP BY ch_model
        """,
        {"models": model_names},
        as_dict=True,
    )
    item_counts_by_model = {row.model: row for row in item_counts}

    for row in models:
        specs = variant_specs_by_sub_category.get(row.sub_category, ())
        combinations = 1 if specs else 0
        for spec in specs:
            value_count = len(spec_values_by_model[row.model].get(spec, ()))
            if not value_count:
                combinations = 0
                break
            combinations *= value_count

        counts = item_counts_by_model.get(row.model, {})
        row["variant_specs"] = len(specs)
        row["expected_items"] = combinations
        row["actual_items"] = int(counts.get("actual_items") or 0)
        row["has_template"] = "Yes" if counts.get("has_template") else "No"

        # Coverage
        if combinations > 0:
            row["coverage_pct"] = round(
                (row["actual_items"] / combinations) * 100, 1
            )
        else:
            row["coverage_pct"] = 100.0 if row["actual_items"] > 0 else 0.0

    return models, truncated
