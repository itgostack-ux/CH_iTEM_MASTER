"""Seed CH Item Master Settings role lists that were still falling back to a code default.

These were legacy free-text fields whose call sites still passed a hardcoded
tuple.  The gate helpers already IGNORED that tuple, so the real policy was the
typed value alone and a blank field already denied everyone.  The fields are now
Table MultiSelect -> CH Role Link.

This copies the typed value across verbatim, so **behaviour is unchanged on
every site**.  A field that is blank stays blank (still deny) -- widening it
would be a policy decision, not a migration.  PREVIOUS_DEFAULTS below records
what the dead call-site tuples said, purely so an administrator can see the
originally-intended role set; it is logged, never applied.

Idempotent.  Roles that do not exist on this site are skipped.
"""
import frappe


DOCTYPE = "CH Item Master Settings"

PREVIOUS_DEFAULTS = {
    "gtin_editor_roles": ('CH GTIN Editor', 'CH Master Approver', 'CH Master Manager', 'System Manager'),
    "mrp_planner_roles": ('CH MRP Planner', 'CH Master Manager', 'System Manager'),
    "plm_manager_roles": ('CH Master Approver', 'CH Master Manager', 'CH PLM Manager', 'System Manager'),
    "vendor_manager_roles": ('CH Master Manager', 'CH Vendor Manager', 'System Manager'),
    "vendor_view_roles": ('CH Master Approver', 'CH Master Manager', 'CH Vendor Manager', 'CH Viewer', 'System Manager'),
    "warranty_claim_finance_roles": ('Accounts Manager', 'CH Warranty Manager'),
    "warranty_claim_logistics_roles": ('CH Warranty Manager', 'Sales Manager', 'Service Manager'),
    "warranty_claim_qc_roles": ('CH Warranty Manager', 'Service Manager', 'Stock Manager', 'Store Manager'),
    "warranty_claim_service_roles": ('CH Warranty Manager', 'Service Manager'),
}


# Ordered escalation ladder — seeded verbatim (order matters), unlike the flat
# role sets above.  Previously hardcoded as RETURN_POLICY_APPROVAL_ROLES.
ORDERED_LADDERS = {
    "return_policy_approval_roles": (
        "CH Zonal Sales Manager",
        "CH Category Head",
        "Sales Manager",
        "CH National Head",
        "COO",
        "CEO",
    ),
}


def _role_settings():
    """Import the ch_erp15 helpers, failing with something actionable.

    This patch lives in ch_item_master but writes role settings owned by ch_erp15, so the
    two apps must be deployed together. A bare module-level import turns a stale
    ch_erp15 into an unreadable ImportError that aborts the whole migrate.
    """
    try:
        from ch_erp15.role_settings import set_setting_roles

        return set_setting_roles
    except ImportError as exc:
        frappe.throw(
            "ch_erp15 is out of date on this site: {0}.\n\n"
            "ch_item_master patches write role settings owned by ch_erp15, so update it "
            "first (it must provide role_settings.set_setting_roles), then re-run "
            "bench migrate.".format(exc),
            title="Update ch_erp15 first",
        )

def execute():
    if not frappe.db.exists("DocType", DOCTYPE) or not frappe.db.table_exists("CH Role Link"):
        return
    set_setting_roles = _role_settings()
    meta = frappe.get_meta(DOCTYPE)
    for fieldname, fallback in PREVIOUS_DEFAULTS.items():
        df = meta.get_field(fieldname)
        if df is None or df.fieldtype != "Table MultiSelect":
            continue
        if frappe.db.count(df.options, {"parent": DOCTYPE, "parenttype": DOCTYPE,
                                        "parentfield": fieldname}):
            continue
        roles = [r for r in _legacy_text_roles(fieldname) if frappe.db.exists("Role", r)]
        if not roles:
            frappe.logger("ch_item_master").info(
                f"{fieldname}: blank before this patch, so it stays blank (deny). "
                f"Originally-shipped roles were {list(fallback)} -- set them in the UI if wanted."
            )
            continue
        set_setting_roles(DOCTYPE, fieldname, roles)

    for fieldname, ladder in ORDERED_LADDERS.items():
        df = meta.get_field(fieldname)
        if df is None or df.fieldtype != "Table MultiSelect":
            continue
        if frappe.db.count(df.options, {"parent": DOCTYPE, "parenttype": DOCTYPE,
                                        "parentfield": fieldname}):
            continue
        roles = [r for r in ladder if frappe.db.exists("Role", r)]
        missing = [r for r in ladder if r not in roles]
        if missing:
            frappe.logger("ch_item_master").info(
                f"{fieldname}: skipped non-existent role(s) {missing}"
            )
        if roles:
            set_setting_roles(DOCTYPE, fieldname, roles)   # order preserved via idx

    frappe.db.commit()


def _legacy_text_roles(fieldname):
    try:
        value = frappe.db.get_value(DOCTYPE, DOCTYPE, fieldname)
    except Exception:
        return []
    if not value:
        return []
    return [p.strip() for p in str(value).replace(",", "\n").split("\n") if p.strip()]
