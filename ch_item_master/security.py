import frappe
from frappe import _


def _is_unrestricted_user(user=None):
    """System managers and Administrator can see all companies."""
    user = user or frappe.session.user
    if not user or user == "Administrator":
        return True

    try:
        return "System Manager" in set(frappe.get_roles(user))
    except Exception:
        return False


def get_user_allowed_companies(user=None):
    """Resolve company scope for a user from explicit mappings and defaults."""
    user = user or frappe.session.user
    if _is_unrestricted_user(user):
        return None

    pos_exec_companies = set()
    if frappe.db.exists("DocType", "POS Executive"):
        try:
            pos_exec_companies.update(filter(None, frappe.get_all(
                "POS Executive",
                filters={"user": user, "is_active": 1},
                pluck="company",
            )))
        except Exception:
            pass

    # For POS-facing users, explicit executive mappings are the source of truth.
    if pos_exec_companies:
        return sorted(pos_exec_companies)

    companies = set()

    try:
        user_permissions = frappe.permissions.get_user_permissions(user) or {}
        for perm in user_permissions.get("Company", []):
            company = perm.get("doc") if isinstance(perm, dict) else None
            if company:
                companies.add(company)
    except Exception:
        pass

    if frappe.db.exists("DocType", "Employee"):
        try:
            companies.update(filter(None, frappe.get_all(
                "Employee",
                filters={"user_id": user, "status": ("!=", "Left")},
                pluck="company",
            )))
        except Exception:
            pass

    try:
        default_company = frappe.defaults.get_user_default("Company", user=user)
    except TypeError:
        try:
            default_company = frappe.defaults.get_user_default("Company")
        except Exception:
            default_company = None
    except Exception:
        default_company = None

    if default_company:
        if isinstance(default_company, (list, tuple, set)):
            companies.update(filter(None, default_company))
        else:
            companies.add(default_company)

    return sorted(companies)


def get_company_scope(requested_company=None, user=None):
    """Return the effective list of companies the user may access."""
    user = user or frappe.session.user
    allowed_companies = get_user_allowed_companies(user)

    if requested_company:
        ensure_company_access(requested_company, user=user)
        return [requested_company]

    return allowed_companies or None


def get_company_filter_value(requested_company=None, user=None):
    """Return a Frappe filter value for the effective company scope."""
    scope = get_company_scope(requested_company=requested_company, user=user)
    if not scope:
        return None
    return scope[0] if len(scope) == 1 else ("in", scope)


def ensure_company_access(company, user=None):
    """Raise PermissionError if a user tries to access another company's data."""
    user = user or frappe.session.user
    if not company or _is_unrestricted_user(user):
        return True

    allowed_companies = get_user_allowed_companies(user)
    if allowed_companies and company not in allowed_companies:
        frappe.throw(
            _("You are not permitted to access {0} data.").format(frappe.bold(company)),
            frappe.PermissionError,
        )
    return True


def get_company_permission_query(doctype, user=None, fieldname="company"):
    """Build permission_query_conditions for company-owned doctypes."""
    user = user or frappe.session.user
    allowed_companies = get_user_allowed_companies(user)
    if not allowed_companies:
        return None

    escaped = ", ".join(frappe.db.escape(company) for company in allowed_companies)
    return f"`tab{doctype}`.`{fieldname}` in ({escaped})"


def has_company_permission(doc=None, user=None, fieldname="company"):
    """Shared doc-level permission check for company-owned doctypes."""
    user = user or frappe.session.user
    if _is_unrestricted_user(user) or doc is None:
        return True

    allowed_companies = get_user_allowed_companies(user)
    if not allowed_companies:
        return True

    company = doc.get(fieldname) if hasattr(doc, "get") else None
    if not company:
        return True

    return company in allowed_companies


def get_warranty_claim_query(user=None):
    return get_company_permission_query("CH Warranty Claim", user=user)


def has_warranty_claim_permission(doc=None, user=None, permission_type=None):
    return has_company_permission(doc=doc, user=user)


def get_sold_plan_query(user=None):
    return get_company_permission_query("CH Sold Plan", user=user)


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
