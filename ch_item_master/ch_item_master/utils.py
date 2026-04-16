from collections import defaultdict
import re

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
        ignore_permissions=True,
    )
    if not variant_specs:
        return []

    if grouped is None:
        grouped = _group_model_spec_values(model)
    return [
        {"spec": row.spec, "values": vals}
        for row in variant_specs
        if (vals := grouped.get(row.spec, []))
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
        ignore_permissions=True,
    )
    if not property_specs:
        return []

    if grouped is None:
        grouped = _group_model_spec_values(model)
    return [
        {"spec": row.spec, "values": grouped.get(row.spec, [])}
        for row in property_specs
    ]


# ---------------------------------------------------------------------------
# Indian phone number validation (canonical home — re-exported by buyback)
# ---------------------------------------------------------------------------

_INDIAN_PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_indian_phone(raw: str) -> str:
    """Strip whitespace, dashes, dots, parentheses, and country-code prefix.

    Accepted input formats (all normalise to a bare 10-digit string):
      - 9989898901
      - 09989898901           (leading 0)
      - +91 9989898901
      - +91-9989898901
      - +919989898901
      - 0091 9989898901
      - +91 98899 89901       (spaces mid-number)
      - +91 9889 989 901      (any internal spacing)

    Returns the normalised 10-digit string, or the stripped input (for the
    caller to reject as invalid).
    """
    s = re.sub(r"[\s\-().]", "", raw or "")
    if s.startswith("+91"):
        s = s[3:]
    elif s.startswith("0091"):
        s = s[4:]
    elif s.startswith("91") and len(s) == 12:
        s = s[2:]
    if s.startswith("0") and len(s) == 11:
        s = s[1:]
    return s


def validate_indian_phone(raw: str, field_label: str = "Mobile number") -> str:
    """Validate and normalise an Indian mobile number.

    Accepts all common Indian formatting variants (see normalize_indian_phone).
    Raises frappe.ValidationError on invalid input.

    Returns the normalised bare 10-digit string.
    """
    from frappe import _

    if not raw or not str(raw).strip():
        frappe.throw(_("{0} is required.").format(field_label), title=_("Validation Error"))

    digits = normalize_indian_phone(str(raw))
    if not _INDIAN_PHONE_RE.match(digits):
        frappe.throw(
            _(
                "{0} '{1}' is not a valid Indian mobile number. "
                "Please enter a 10-digit number starting with 6–9 "
                "(e.g. 9876543210, +91 9876543210, 09876543210)."
            ).format(field_label, raw)
        )
    return digits
