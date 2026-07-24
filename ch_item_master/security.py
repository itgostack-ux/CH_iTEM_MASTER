import frappe
from frappe import _

from ch_item_master.config import (
    get_role_setting,
    has_role_setting,
    is_privileged_user,
    require_role_setting,
)


def require_scoped_document_action(
    doc,
    role_field,
    default_roles=(),
    action=None,
    permission_types=("write",),
    company_field="company",
    store_field=None,
    lock=False,
    user=None,
):
    """Authorize a sensitive bound document action and optionally lock its row."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("You must be signed in to perform this action."), frappe.PermissionError)

    privileged = is_privileged_user(user)
    if not privileged and role_field:
        require_role_setting(role_field, default_roles, action=action)

    if lock:
        if not doc.get("name"):
            frappe.throw(_("Save the document before performing this action."), frappe.ValidationError)
        locked_name = frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
        if not locked_name:
            frappe.throw(_("Document {0} no longer exists.").format(doc.name), frappe.DoesNotExistError)
        doc.reload()

    if privileged:
        return

    if isinstance(permission_types, str):
        permission_types = (permission_types,)
    for permission_type in permission_types:
        if not frappe.has_permission(
            doc.doctype,
            ptype=permission_type,
            doc=doc,
            user=user,
            throw=False,
        ):
            frappe.throw(
                _("You do not have {0} permission for {1}.").format(
                    permission_type, doc.name
                ),
                frappe.PermissionError,
            )

    company = doc.get(company_field) if company_field else None
    store = doc.get(store_field) if store_field else None
    if not company_field and not store_field:
        return

    if company_field:
        ensure_company_access(company, user=user)

    try:
        from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
    except (ImportError, ModuleNotFoundError):
        frappe.throw(
            _("Location scope validation is unavailable. Contact an administrator."),
            frappe.PermissionError,
        )

    assert_user_has_store_scope(
        store=store,
        company=company,
        user=user,
        msg=_("You are not permitted to act on this company or store."),
    )


def _is_unrestricted_user(user=None):
    """Return whether the user has a configured company-scope bypass role."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return False
    if is_privileged_user(user):
        return True

    try:
        bypass_roles = get_role_setting("company_scope_bypass_roles", ("System Manager",))
        return bool(set(frappe.get_roles(user)).intersection(bypass_roles))
    except Exception:
        return False


def _get_scope_mapped_companies(user):
    """Companies resolved from the user's CH User Scope (hub RBAC, ch_erp15).

    The scope resolver derives companies from explicit company rows as well as
    city/zone/store rows, so a Store Executive scoped to one store maps to that
    store's company. Imported lazily: ch_item_master installs before ch_erp15,
    and ch_erp15.company_lock already imports this module.
    """
    try:
        if not frappe.db.exists("DocType", "CH User Scope"):
            return set()
        from ch_erp15.ch_erp15.scope import get_user_scope

        scope = get_user_scope(user)
    except Exception:
        return set()

    if not scope or scope.get("bypass"):
        return set()
    return {c for c in (scope.get("companies") or set()) if c}


def get_user_mapped_companies(user=None):
    """Resolve explicit company mappings without using defaults as access grants.

    ``None`` means the user is intentionally unrestricted. A list (including an
    empty list) is an explicit scope. POS mappings take precedence for POS-facing
    users, followed by Company User Permissions, then active Employee mappings.
    Companies granted through CH User Scope are unioned into every tier — the
    scope doctype is an explicit admin grant of the same standing, and hubs,
    notifications and the Desk company switcher must agree on membership.
    """
    user = user or frappe.session.user
    if _is_unrestricted_user(user):
        return None

    scope_companies = _get_scope_mapped_companies(user)

    pos_companies = set()
    if frappe.db.exists("DocType", "POS Executive"):
        try:
            pos_companies.update(filter(None, frappe.get_all(
                "POS Executive",
                filters={"user": user, "is_active": 1},
                pluck="company",
            )))
        except Exception:
            pass

    if frappe.db.exists("DocType", "CH POS User Allocation"):
        try:
            pos_companies.update(filter(None, frappe.get_all(
                "CH POS User Allocation",
                filters={"user": user, "is_active": 1},
                pluck="company",
            )))
        except Exception:
            pass

    # POS allocation is the source of truth for a POS-facing user; CH User
    # Scope grants of equal standing are added, never overridden.
    if pos_companies:
        return sorted(pos_companies | scope_companies)

    companies = set()
    try:
        user_permissions = frappe.permissions.get_user_permissions(user) or {}
        for perm in user_permissions.get("Company", []):
            company = perm.get("doc") if isinstance(perm, dict) else None
            if company:
                companies.add(company)
    except Exception:
        pass

    # Explicit Company User Permissions override the Employee fallback.
    if companies:
        return sorted(companies | scope_companies)

    if frappe.db.exists("DocType", "Employee"):
        try:
            companies.update(filter(None, frappe.get_all(
                "Employee",
                filters={"user_id": user, "status": ("!=", "Left")},
                pluck="company",
            )))
        except Exception:
            pass

    return sorted(companies | scope_companies)


def get_user_allowed_companies(user=None):
    """Resolve company scope from explicit mappings only.

    ``None`` is reserved for an unrestricted privileged user. An empty list is
    an explicit denial and must never be widened by a user default.
    """
    user = user or frappe.session.user
    return get_user_mapped_companies(user)


def get_company_scope(requested_company=None, user=None):
    """Return the effective list of companies the user may access."""
    user = user or frappe.session.user
    allowed_companies = get_user_allowed_companies(user)

    if requested_company:
        ensure_company_access(requested_company, user=user)
        return [requested_company]

    return allowed_companies


def get_company_filter_value(requested_company=None, user=None):
    """Return a Frappe filter value for the effective company scope."""
    scope = get_company_scope(requested_company=requested_company, user=user)
    if scope is None:
        return None
    if not scope:
        return ("in", ["__no_company_scope__"])
    return scope[0] if len(scope) == 1 else ("in", scope)


def ensure_company_access(company, user=None):
    """Raise PermissionError if a user tries to access another company's data."""
    user = user or frappe.session.user
    if _is_unrestricted_user(user):
        return True

    if not company:
        frappe.throw(_("A company is required for this operation."), frappe.PermissionError)

    allowed_companies = get_user_allowed_companies(user)
    if not allowed_companies or company not in allowed_companies:
        frappe.throw(
            _("You are not permitted to access {0} data.").format(frappe.bold(company)),
            frappe.PermissionError,
        )
    return True


def get_company_permission_query(doctype, user=None, fieldname="company"):
    """Build permission_query_conditions for company-owned doctypes."""
    user = user or frappe.session.user
    allowed_companies = get_user_allowed_companies(user)
    if allowed_companies is None:
        return None
    if not allowed_companies:
        return "1=0"

    escaped = ", ".join(frappe.db.escape(company) for company in allowed_companies)
    return f"`tab{doctype}`.`{fieldname}` in ({escaped})"


def has_company_permission(doc=None, user=None, fieldname="company"):
    """Shared doc-level permission check for company-owned doctypes."""
    user = user or frappe.session.user
    if _is_unrestricted_user(user):
        return True

    allowed_companies = get_user_allowed_companies(user)
    if not allowed_companies:
        return False

    if doc is None:
        return True

    company = doc.get(fieldname) if hasattr(doc, "get") else None
    if not company:
        return False

    return company in allowed_companies


def get_warranty_claim_query(user=None):
    return get_company_permission_query("CH Warranty Claim", user=user)


def has_warranty_claim_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_sold_plan_query(user=None):
    return get_company_permission_query("Active VAS Plans", user=user)


def has_sold_plan_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_customer_device_query(user=None):
    return get_company_permission_query("CH Customer Device", user=user)


def has_customer_device_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_warranty_plan_query(user=None):
    return get_company_permission_query("CH Warranty Plan", user=user)


def has_warranty_plan_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_exception_request_query(user=None):
    return get_company_permission_query("CH Exception Request", user=user)


def has_exception_request_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_price_upload_batch_query(user=None):
    return get_company_permission_query("CH Price Upload Batch", user=user)


def has_price_upload_batch_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_scheme_receivable_query(user=None):
    return get_company_permission_query("CH Scheme Receivable", user=user)


def has_scheme_receivable_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_scheme_document_upload_query(user=None):
    return get_company_permission_query("Scheme Document Upload", user=user)


def has_scheme_document_upload_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_supplier_scheme_circular_query(user=None):
    return get_company_permission_query("Supplier Scheme Circular", user=user)


def has_supplier_scheme_circular_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_scheme_product_map_query(user=None):
    return get_company_permission_query("Scheme Product Map", user=user)


def has_scheme_product_map_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_vendor_info_record_query(user=None):
    return get_company_permission_query("CH Vendor Info Record", user=user)


def has_vendor_info_record_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_vendor_performance_query(user=None):
    return get_company_permission_query("CH Vendor Performance", user=user)


def has_vendor_performance_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


_ITEM_APP_ROLES = (
    "CH Master Manager",
    "CH Price Manager",
    "CH Offer Manager",
    "CH Warranty Manager",
    "CH Viewer",
    "Stock User",
)


def _get_serial_scope(user=None):
    user = user or frappe.session.user
    if is_privileged_user(user):
        return None
    try:
        from ch_erp15.ch_erp15.scope import get_user_scope

        scope = get_user_scope(user) or {}
    except (ImportError, ModuleNotFoundError):
        return {"companies": set(), "warehouses": set()}
    if scope.get("bypass"):
        return None
    return {
        "companies": set(scope.get("companies") or ()),
        "warehouses": set(scope.get("warehouses") or ()),
    }


def get_serial_lifecycle_query(user=None):
    user = user or frappe.session.user
    if not has_role_setting("app_access_roles", _ITEM_APP_ROLES, user=user):
        return "1=0"
    scope = _get_serial_scope(user)
    if scope is None:
        return None
    companies = scope["companies"]
    warehouses = scope["warehouses"]
    if not companies or not warehouses:
        return "1=0"
    company_sql = ", ".join(frappe.db.escape(value) for value in sorted(companies))
    warehouse_sql = ", ".join(frappe.db.escape(value) for value in sorted(warehouses))
    return (
        f"`tabCH Serial Lifecycle`.`current_company` IN ({company_sql}) AND "
        f"`tabCH Serial Lifecycle`.`current_warehouse` IN ({warehouse_sql})"
    )


def has_serial_lifecycle_permission(doc=None, user=None, permission_type=None):
    user = user or frappe.session.user
    if not has_role_setting("app_access_roles", _ITEM_APP_ROLES, user=user):
        return False
    scope = _get_serial_scope(user)
    if scope is None:
        return True
    if doc is None:
        return True
    return bool(
        doc.get("current_company") in scope["companies"]
        and doc.get("current_warehouse") in scope["warehouses"]
    )


def get_item_version_query(user=None):
    user = user or frappe.session.user
    if not has_role_setting("app_access_roles", _ITEM_APP_ROLES, user=user):
        return "1=0"
    from ch_item_master.ch_item_master.rbac import get_item_query

    item_condition = get_item_query(user)
    if not item_condition:
        return None
    return (
        "`tabCH Item Version`.`item_code` IN "
        f"(SELECT `name` FROM `tabItem` WHERE {item_condition})"
    )


def has_item_version_permission(doc=None, user=None, permission_type=None):
    user = user or frappe.session.user
    if not has_role_setting("app_access_roles", _ITEM_APP_ROLES, user=user):
        return False
    if doc is None:
        return True
    item_code = doc.get("item_code")
    return bool(
        item_code
        and frappe.has_permission("Item", ptype="read", doc=item_code, user=user)
    )
