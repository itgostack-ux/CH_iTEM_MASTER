# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ch_item_master.constants.custom_fields import CUSTOM_FIELDS

CH_ROLES = [
    {"role_name": "CH Master Manager",   "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Price Manager",    "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Offer Manager",    "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Warranty Manager", "desk_access": 1, "is_custom": 1},
    {"role_name": "CH Viewer",           "desk_access": 1, "is_custom": 1},
]


def create_ch_custom_fields():
    """Create or update custom fields on ERPNext doctypes (Brand, Item, Item Group)."""
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def setup_roles():
    """Create CH-specific roles if they don't already exist."""
    import frappe

    for role_def in CH_ROLES:
        if not frappe.db.exists("Role", role_def["role_name"]):
            doc = frappe.new_doc("Role")
            doc.update(role_def)
            doc.insert(ignore_permissions=True)
    # Note: do NOT call frappe.db.commit() here — after_install manages the
    # outer transaction; an explicit commit here would cut it short prematurely.


def setup_item_variant_settings():
    """Add CH custom fields to 'Copy Fields to Variant' in Item Variant Settings.

    This ensures ERPNext's native variant creation copies ch_model, ch_sub_category,
    ch_category, and ch_display_name from template to variant.
    """
    import frappe

    ch_fields = ["ch_model", "ch_sub_category", "ch_category", "ch_display_name", "ch_spec_values"]

    settings = frappe.get_single("Item Variant Settings")
    existing = {row.field_name for row in settings.fields}

    for field_name in ch_fields:
        if field_name not in existing:
            settings.append("fields", {"field_name": field_name})

    settings.save(ignore_permissions=True)
    # Note: Commit is handled by the calling function (after_install/after_migrate)


# ── Default channels created on install/migrate ───────────────────────────────
_DEFAULT_CHANNELS = [
    {"channel_name": "POS",         "description": "Point of Sale",                 "is_active": 1},
    {"channel_name": "Website",     "description": "Web Store",                     "is_active": 1},
    {"channel_name": "App",         "description": "Mobile App",                    "is_active": 1},
    {"channel_name": "Marketplace", "description": "3rd Party Marketplace",         "is_active": 1},
    {"channel_name": "Buyback",     "description": "Device Buyback / Trade-In",     "is_active": 1, "buying": 1},
]


def sync_workspace():
    """Force-push workspace JSON file content into the DB record.

    Frappe only syncs workspace on first install; subsequent changes to the file
    are ignored unless this is called explicitly.
    """
    import json
    import os
    import frappe

    json_path = os.path.join(
        frappe.get_app_path("ch_item_master"),
        "ch_item_master", "workspace", "ch_item_master", "ch_item_master.json",
    )
    with open(json_path) as f:
        file_data = json.load(f)

    ws = frappe.get_doc("Workspace", "CH Item Master")
    ws.content = file_data["content"]
    ws.links = []
    ws.shortcuts = []

    for lnk in file_data.get("links", []):
        ws.append("links", lnk)
    for sc in file_data.get("shortcuts", []):
        ws.append("shortcuts", sc)

    ws.save(ignore_permissions=True)
    # Note: Commit is handled by the calling context
    frappe.msgprint(
        f"Workspace synced — {len(ws.shortcuts)} shortcuts, {len(ws.links)} links."
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
            doc.is_active = ch.get("is_active", 1)
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
