# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
#
# Grant read permission on the ERPNext `Manufacturer` doctype to CH Item Master
# roles so that Link field search works for users who are not Stock User/Manager.

import frappe

_ROLES = ("Item Manager", "CH Item Reviewer", "CH Item Creator")


def execute():
    for role in _ROLES:
        # Skip if this role doesn't exist in the site
        if not frappe.db.exists("Role", role):
            continue
        # Skip if a DocPerm already exists for this role on Manufacturer
        exists = frappe.db.exists(
            "DocPerm",
            {"parent": "Manufacturer", "role": role, "read": 1},
        )
        if exists:
            continue
        frappe.get_doc({
            "doctype": "DocPerm",
            "parent": "Manufacturer",
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": role,
            "read": 1,
            "write": 0,
            "create": 0,
            "delete": 0,
            "submit": 0,
            "cancel": 0,
            "amend": 0,
            "report": 1,
            "export": 0,
            "import": 0,
            "share": 0,
            "print": 0,
            "email": 0,
        }).insert(ignore_permissions=True)

    frappe.clear_cache(doctype="Manufacturer")
