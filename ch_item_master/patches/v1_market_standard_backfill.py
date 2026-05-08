"""Patch: Market-standard Item Master backfill (FIX-7, FIX-8, FIX-9).

Executed after model sync so all new fields exist.

1. Set status='Active' for all existing CH Sub Category records
2. Set status='Active' for all existing CH Model records
3. Backfill spec_type on CH Sub Category Spec rows from is_variant
4. Set feature_type='Technical' for all existing CH Feature records without one
5. Backfill ch_spec_values with first model value for items that have blank spec_value
6. Uppercase all existing sub-category prefixes
"""

import frappe


def execute():
    # 1. Status Active on all existing Sub Categories
    frappe.db.sql(
        "UPDATE `tabCH Sub Category` SET status = 'Active' WHERE COALESCE(status, '') = ''"
    )

    # 2. Status Active on all existing Models
    frappe.db.sql(
        "UPDATE `tabCH Model` SET status = 'Active' WHERE COALESCE(status, '') = ''"
    )

    # 3. Backfill spec_type on CH Sub Category Spec child rows
    frappe.db.sql(
        """
        UPDATE `tabCH Sub Category Spec`
        SET spec_type = CASE WHEN is_variant = 1 THEN 'Variant' ELSE 'Property' END
        WHERE COALESCE(spec_type, '') = ''
        """
    )

    # 4. Feature type default for existing CH Feature records
    frappe.db.sql(
        "UPDATE `tabCH Feature` SET feature_type = 'Technical' WHERE COALESCE(feature_type, '') = ''"
    )

    # 5. Backfill sub-category prefix to UPPER
    frappe.db.sql(
        "UPDATE `tabCH Sub Category` SET prefix = UPPER(prefix) WHERE prefix IS NOT NULL"
    )

    # 6. Backfill blank spec_value rows on CH Item Spec Value
    # For each item that has spec rows with blank spec_value, fill from the model's
    # spec_values table (first value for that spec).
    items_with_blank = frappe.db.sql(
        """
        SELECT DISTINCT sv.parent AS item_name
        FROM `tabCH Item Spec Value` sv
        WHERE COALESCE(sv.spec_value, '') = ''
        """,
        as_dict=True,
    )

    for row in items_with_blank:
        item_name = row.item_name
        ch_model = frappe.db.get_value("Item", item_name, "ch_model")
        if not ch_model:
            continue

        # Get spec→first_value map from the model
        model_vals = frappe.db.sql(
            """
            SELECT spec, spec_value
            FROM `tabCH Model Spec Value`
            WHERE parent = %s
            ORDER BY idx
            """,
            ch_model,
            as_dict=True,
        )
        # Use first occurrence per spec
        spec_first = {}
        for mv in model_vals:
            if mv.spec not in spec_first and mv.spec_value:
                spec_first[mv.spec] = mv.spec_value

        if not spec_first:
            continue

        for spec, val in spec_first.items():
            frappe.db.sql(
                """
                UPDATE `tabCH Item Spec Value`
                SET spec_value = %s
                WHERE parent = %s AND spec = %s AND COALESCE(spec_value, '') = ''
                """,
                (val, item_name, spec),
            )

    frappe.db.commit()
