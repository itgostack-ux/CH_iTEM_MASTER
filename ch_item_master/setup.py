# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ch_item_master.constants.custom_fields import CUSTOM_FIELDS
from ch_item_master.ch_customer_master.customer_custom_fields import CUSTOMER_CUSTOM_FIELDS

CH_ROLES = [
    # Tier-0: management / approval
    {"role_name": "CH Master Manager",   "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Master Approver",  "desk_access": 1, "is_custom": 1},
    # Tier-1: function-specific
    {"role_name": "CH Price Manager",    "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Offer Manager",    "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Warranty Manager", "desk_access": 1, "is_custom": 1},
    # Tier-2: RBAC-parity granular roles (Oracle/SAP level)
    {"role_name": "CH Item Creator",     "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Item Reviewer",    "desk_access": 1, "is_custom": 1},
    {"role_name": "CH PLM Manager",      "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Vendor Manager",   "desk_access": 1, "is_custom": 1},
    {"role_name": "CH MRP Planner",      "desk_access": 1, "is_custom": 1},
    {"role_name": "CH GTIN Editor",      "desk_access": 1, "is_custom": 1},
    # Read-only viewer
    {"role_name": "CH Viewer",           "desk_access": 1, "is_custom": 1},
    # Logistics manager — manages online/bot claim pickup queue only
    {"role_name": "CH Logistics Manager", "desk_access": 1, "is_custom": 1},
]


def create_ch_custom_fields():
    """Create or update custom fields on ERPNext doctypes (Brand, Item, Item Group, Customer).

    update=True ensures code changes (options, permlevel, etc.) are pushed to existing installs.
    """
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
    create_custom_fields(CUSTOMER_CUSTOM_FIELDS, ignore_validate=True, update=True)


def setup_roles():
    """Create CH-specific roles if they don't already exist, then install field-level DocPerms."""
    import frappe

    for role_def in CH_ROLES:
        if not frappe.db.exists("Role", role_def["role_name"]):
            doc = frappe.new_doc("Role")
            doc.update(role_def)
            doc.insert(ignore_permissions=True)

    # Install permlevel-1 Custom DocPerm records for sensitive Item fields
    try:
        from ch_item_master.ch_item_master.rbac import install_custom_docperms
        install_custom_docperms()
    except Exception:
        frappe.log_error(title="setup_roles: install_custom_docperms failed", message=frappe.get_traceback())
    try:
        from ch_erp15.ch_erp15.default_permissions import seed_default_docperms

        seed_default_docperms({
            "Customer": {
                "CH Master Manager": {"read", "write"},
                "CH Master Approver": {"read", "write"},
                "CH Warranty Manager": {"read"},
                "Service Manager": {"read"},
            },
            "Company": {
                "Sales Manager": {"read"},
                "Marketing Manager": {"read"},
                "CH Master Manager": {"read"},
                "CH Price Manager": {"read"},
                "CH Viewer": {"read"},
                "Stock Manager": {"read"},
                "Stock User": {"read"},
                "Store Manager": {"read"},
                "Store Executive": {"read"},
            },
            "Warehouse": {
                "CH Master Manager": {"read", "write", "create", "delete"},
                "Stock Manager": {"read"},
                "Stock User": {"read"},
                "Store Manager": {"read"},
                "Store Executive": {"read"},
            },
            "Branch": {
                "CH Master Manager": {"read", "write", "create", "delete"},
            },
            "POS Profile": {
                "CH Master Manager": {"read", "write", "create"},
            },
            "Country": {
                "CH Master Manager": {"read"},
            },
            "Sales Invoice": {
                "Sales Manager": {"read"},
                "Marketing Manager": {"read"},
                "CH Master Manager": {"read"},
            },
            "POS Invoice": {
                "Sales Manager": {"read"},
                "CH Master Manager": {"read"},
            },
            "Coupon Code": {
                "Sales Manager": {"read"},
                "CH Master Manager": {"read"},
            },
            "Service Request": {
                "Sales Manager": {"read"},
                "Marketing Manager": {"read"},
                "CH Master Manager": {"read"},
            },
            "Item": {
                "CH Master Manager": {"read"},
                "CH Price Manager": {"read"},
                "CH Offer Manager": {"read"},
                "CH Warranty Manager": {"read"},
                "CH Viewer": {"read"},
                "Stock User": {"read"},
                "Service Manager": {"read"},
                "Sales Manager": {"read"},
            },
            "Manufacturer": {
                "CH Master Manager": {"read"},
                "CH Price Manager": {"read"},
                "CH Offer Manager": {"read"},
                "CH Warranty Manager": {"read"},
                "CH Viewer": {"read"},
                "Stock User": {"read"},
            },
            "Brand": {
                "CH Master Manager": {"read"},
                "CH Price Manager": {"read"},
                "CH Offer Manager": {"read"},
                "CH Warranty Manager": {"read"},
                "CH Viewer": {"read"},
                "Stock User": {"read"},
            },
        })
    except Exception:
        frappe.log_error(title="setup_roles: default DocPerm setup failed", message=frappe.get_traceback())
    # Note: do NOT call frappe.db.commit() here — after_install manages the
    # outer transaction; an explicit commit here would cut it short prematurely.


def setup_item_variant_settings():
    """Add CH custom fields to 'Copy Fields to Variant' in Item Variant Settings.

    This ensures ERPNext's native variant creation copies ch_model, ch_sub_category,
    ch_category, display/spec data, and the item-level MRP from template to variant.
    """
    import frappe

    ch_fields = [
        "ch_model",
        "ch_sub_category",
        "ch_category",
        "ch_display_name",
        "ch_item_mrp",
        "ch_spec_values",
        "ch_model_features",
    ]

    settings = frappe.get_single("Item Variant Settings")
    existing = {row.field_name for row in settings.fields}

    for field_name in ch_fields:
        if field_name not in existing:
            settings.append("fields", {"field_name": field_name})

    settings.save(ignore_permissions=True)
    # Note: Commit is handled by the calling function (after_install/after_migrate)


# ── Default channels created on install/migrate ───────────────────────────────
_DEFAULT_CHANNELS = [
    {"channel_name": "POS",         "description": "Point of Sale",                 "disabled": 0},
    {"channel_name": "Website",     "description": "Web Store",                     "disabled": 0},
    {"channel_name": "App",         "description": "Mobile App",                    "disabled": 0},
    {"channel_name": "Marketplace", "description": "3rd Party Marketplace",         "disabled": 0},
    {"channel_name": "Buyback",     "description": "Device Buyback / Trade-In",     "disabled": 0, "buying": 1},
]


def sync_workspace():
    """Force-push workspace JSON file content into the DB record.

    Frappe only syncs workspace on first install; subsequent changes to the file
    are ignored unless this is called explicitly.
    """
    import json
    import os
    import frappe

    workspaces = [
        {
            "module": "ch_item_master",
            "subfolder": os.path.join("ch_item_master", "workspace", "ch_core"),
            "filename": "ch_core.json",
            "label": "CH Core",
        },
        {
            "module": "ch_item_master",
            "subfolder": os.path.join("ch_item_master", "workspace", "ch_item_master"),
            "filename": "ch_item_master.json",
            "label": "CH Item Master",
        },
        {
            "module": "ch_customer_master",
            "subfolder": os.path.join("ch_customer_master", "workspace", "ch_customer_master"),
            "filename": "ch_customer_master.json",
            "label": "CH Customer Master",
        },
        {
            "module": "ch_vendor_master",
            "subfolder": os.path.join("ch_vendor_master", "workspace", "ch_vendor_master"),
            "filename": "ch_vendor_master.json",
            "label": "CH Vendor Master",
        },
        {
            "module": "ch_item_master",
            "subfolder": os.path.join("ch_item_master", "workspace", "ch_vas"),
            "filename": "ch_vas.json",
            "label": "CH VAS",
        },
    ]

    for ws_def in workspaces:
        json_path = os.path.join(
            frappe.get_app_path("ch_item_master"),
            ws_def["subfolder"],
            ws_def["filename"],
        )
        if not os.path.exists(json_path):
            continue

        with open(json_path) as f:
            file_data = json.load(f)

        if not frappe.db.exists("Workspace", ws_def["label"]):
            # Create workspace from JSON if it doesn't exist yet
            ws = frappe.new_doc("Workspace")
            ws.label = ws_def["label"]
            ws.name = ws_def["label"]
        else:
            ws = frappe.get_doc("Workspace", ws_def["label"])

        ws.content = file_data.get("content", "")
        ws.icon = file_data.get("icon", "")
        ws.indicator_color = file_data.get("indicator_color", "blue")
        ws.module = file_data.get("module", ws_def.get("module", ""))
        ws.parent_page = file_data.get("parent_page", "")
        ws.public = file_data.get("public", 1)
        ws.title = file_data.get("title", ws_def["label"])
        ws.type = file_data.get("type") or "Workspace"
        ws.links = []
        ws.shortcuts = []

        for lnk in file_data.get("links", []):
            ws.append("links", lnk)
        for sc in file_data.get("shortcuts", []):
            ws.append("shortcuts", sc)

        ws.flags.ignore_links = True
        ws.save(ignore_permissions=True)
        frappe.logger("ch_item_master").info(
            f"Workspace '{ws_def['label']}' synced — {len(ws.shortcuts)} shortcuts, {len(ws.links)} links."
        )


def setup_channels():
    """Ensure default CH Price Channels and matching ERPNext Price Lists exist.

    ERPNext uses Price Lists natively in all selling/buying transactions. Each channel
    (POS / Website / App / Marketplace / Buyback) must have a corresponding Price List so
    that CH Item Price.on_update() can sync into ERPNext Item Price and the rate
    is auto-filled on Sales Invoice, Sales Order, POS, Quotation, Purchase Invoice, etc.
    """
    import frappe

    for ch in _DEFAULT_CHANNELS:
        # 1. Ensure the ERPNext Price List exists
        pl_name = f"CH {ch['channel_name']}"
        is_buying = ch.get("buying", 0)
        if not frappe.db.exists("Price List", pl_name):
            pl = frappe.new_doc("Price List")
            pl.price_list_name = pl_name
            pl.currency = "INR"
            pl.selling = 0 if is_buying else 1
            pl.buying = 1 if is_buying else 0
            pl.enabled = 1
            pl.insert(ignore_permissions=True)

        # 2. Create the CH Price Channel linked to the Price List
        if not frappe.db.exists("CH Price Channel", ch["channel_name"]):
            doc = frappe.new_doc("CH Price Channel")
            doc.channel_name = ch["channel_name"]
            doc.description = ch.get("description", "")
            doc.disabled = ch.get("disabled", 0)
            doc.price_list = pl_name
            doc.is_buying = is_buying
            doc.insert(ignore_permissions=True)
        else:
            # Back-fill price_list and is_buying if empty
            existing_pl = frappe.db.get_value("CH Price Channel", ch["channel_name"], "price_list")
            if not existing_pl:
                frappe.db.set_value(
                    "CH Price Channel", ch["channel_name"], "price_list", pl_name,
                    update_modified=False,
                )
            if is_buying:
                frappe.db.set_value(
                    "CH Price Channel", ch["channel_name"], "is_buying", 1,
                    update_modified=False,
                )

    # Note: Commit is handled by the calling function (after_migrate)


def setup_vas_settings():
    """Ensure CH VAS Settings singleton exists with defaults.

    Called after_migrate to bootstrap the settings record.
    """
    import frappe

    if not frappe.db.exists("CH VAS Settings"):
        doc = frappe.new_doc("CH VAS Settings")
        default_company = frappe.db.get_single_value("Global Defaults", "default_company")
        if default_company and frappe.db.exists("Company", default_company):
            doc.gogizmo_company = default_company
        if frappe.db.exists("DocType", "GoFix Settings"):
            gofix_company = frappe.db.get_single_value("GoFix Settings", "operating_company")
            if gofix_company and frappe.db.exists("Company", gofix_company):
                doc.gofix_company = gofix_company
        doc.flags.ignore_links = True
        doc.flags.ignore_mandatory = True
        doc.insert(ignore_permissions=True)
        frappe.logger("ch_item_master").info("CH VAS Settings created with defaults")


def seed_stock_count_variance_exception_type():
    """Ensure the 'Stock Count Variance' CH Exception Type exists.

    Raised by CH Cycle Count when a physical count variance exceeds tolerance;
    routed through the CH Approval Authority matrix before any Stock
    Reconciliation (ledger/accounting) adjustment is posted. Idempotent.
    """
    import frappe

    name = "Stock Count Variance"
    if not frappe.db.exists("DocType", "CH Exception Type"):
        return
    if frappe.db.exists("CH Exception Type", name):
        return
    try:
        doc = frappe.get_doc({
            "doctype": "CH Exception Type",
            "exception_type": name,
            "enabled": 1,
            "routing_mode": "Approval Matrix",
            "requires_otp": 0,
            "max_value_without_approval": 0,
            "validity_minutes": 1440,
            "escalation_sla_minutes": 120,
            "applicable_to_ggr": 1,
            "applicable_to_gfs": 1,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger("ch_item_master").info("Seeded 'Stock Count Variance' CH Exception Type")
    except Exception:
        frappe.log_error(title="seed Stock Count Variance exception type failed",
                         message=frappe.get_traceback())


def seed_external_device_item():
    """Ensure the 'EXTERNAL-DEVICE' generic item exists and is set as the
    company-level default for external IMEI VAS plan sales.

    This is a non-stock placeholder — the actual device identity is always
    captured via the IMEI in the serial_no field. Idempotent.
    """
    import frappe
    from frappe.utils import now

    item_code = "EXTERNAL-DEVICE"
    if not frappe.db.exists("Item", item_code):
        try:
            item_group = (
                frappe.db.get_value("Item Group", {"name": "Services"}, "name")
                or frappe.db.get_value("Item Group", {"name": "All Item Groups"}, "name")
                or "All Item Groups"
            )
            now_ts = now()
            frappe.db.sql("""
                INSERT INTO `tabItem`
                    (name, item_code, item_name, item_group, stock_uom,
                     is_stock_item, is_fixed_asset, has_serial_no,
                     include_item_in_manufacturing, disabled,
                     gst_hsn_code, description,
                     creation, modified, modified_by, owner, docstatus)
                VALUES
                    (%s, %s, %s, %s, %s,
                     0, 0, 0,
                     0, 0,
                     %s, %s,
                     %s, %s, 'Administrator', 'Administrator', 0)
            """, (
                item_code, item_code, "External Customer Device", item_group, "Nos",
                "998717",
                "Generic placeholder item for customer-provided devices (external IMEI). "
                "Used on Active VAS Plans when selling warranty/protection plans for devices "
                "not purchased from GoGizmo. Device identity is always captured in serial_no (IMEI).",
                now_ts, now_ts,
            ))
            frappe.db.commit()
            frappe.logger("ch_item_master").info("Seeded EXTERNAL-DEVICE item")
        except Exception:
            frappe.log_error(title="seed_external_device_item failed", message=frappe.get_traceback())
            return

    # Backfill ch_default_external_device_item on all companies that don't have it set
    if not frappe.db.has_column("Company", "ch_default_external_device_item"):
        return

    companies = frappe.get_all("Company", fields=["name", "ch_default_external_device_item"])
    for company in companies:
        if not company.get("ch_default_external_device_item"):
            try:
                frappe.db.set_value("Company", company.name, "ch_default_external_device_item", item_code)
                frappe.logger("ch_item_master").info(
                    f"Set ch_default_external_device_item=EXTERNAL-DEVICE on company {company.name}"
                )
            except Exception:
                frappe.log_error(
                    title=f"seed_external_device_item: could not update company {company.name}",
                    message=frappe.get_traceback(),
                )


def delete_ch_custom_fields():
    """Remove custom fields created by CH Item Master. Called on uninstall."""
    import frappe

    for doctype, fields in CUSTOM_FIELDS.items():
        if isinstance(doctype, tuple):
            doctypes = doctype
        else:
            doctypes = (doctype,)

        for dt in doctypes:
            for field in fields:
                fieldname = field.get("fieldname")
                if fieldname and frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fieldname}):
                    frappe.delete_doc("Custom Field", f"{dt}-{fieldname}", force=True)

    # Note: Commit is handled by the uninstall process
