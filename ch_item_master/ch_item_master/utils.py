from collections import defaultdict

import frappe

# Roles that are allowed to access the CH Item Master app
CH_APP_ROLES = frozenset([
	"CH Master Manager",
	"CH Price Manager",
	"CH Offer Manager",
	"CH Warranty Manager",
	"CH Viewer",
])


def check_app_permission():
	"""Check if user has a CH-specific role to access the CH Item Master app.

	Returns True for Administrator unconditionally; for all others the user must
	have at least one of the CH_APP_ROLES — generic Item read access is NOT
	sufficient (prevents all ERPNext stock users from landing here).
	"""
	if frappe.session.user == "Administrator":
		return True

	user_roles = set(frappe.get_roles(frappe.session.user))
	if user_roles & CH_APP_ROLES:
		return True

	return False


# ───────────────────────────────────────────────────────────────────────────────
# Item Code helpers (moved from api.py — used by overrides/item.py)
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
# Spec helpers (moved from api.py — used by api.get_model_details etc.)
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
    """Return variant specs (is_variant=1) and their allowed values from the model.

    Every spec with is_variant=1 becomes a variant axis in ERPNext's variant
    system — each combination of values creates a separate Item (SKU).
    This includes specs like Colour that don't affect pricing but still
    need their own item codes.

    Returns: list of {spec, values: ['Black', 'White', ...]}
    """
    variant_specs = frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": sub_category, "parenttype": "CH Sub Category",
                 "is_variant": 1},
        fields=["spec", "name_order"],
        order_by="name_order asc, idx asc",
    )
    if not variant_specs:
        return []

    if grouped is None:
        grouped = _group_model_spec_values(model)
    return [
        {"spec": row.spec, "values": grouped.get(row.spec, [])}
        for row in variant_specs
    ]


def _get_property_specs(sub_category, model, grouped=None):
    """Return all specs that do NOT drive variant creation (is_variant=0).

    These are shared properties stored in ch_spec_values on the item,
    not used as variant axes (no separate Item per value).

    Returns: list of {spec, values: ['Dual SIM', ...]}
    """
    property_specs = frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": sub_category, "parenttype": "CH Sub Category",
                 "is_variant": 0},
        fields=["spec"],
        order_by="idx asc",
    )
    if not property_specs:
        return []

    if grouped is None:
        grouped = _group_model_spec_values(model)
    return [
        {"spec": row.spec, "values": grouped.get(row.spec, [])}
        for row in property_specs
    ]
