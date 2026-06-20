# Copyright (c) 2026, GoStack and contributors
"""Migrate CH Exception Type.approval_level → routing_mode.

The flat `approval_level` Select (Store Manager … MD, Category Manager) has been
replaced by `routing_mode` (Approval Matrix | Category Manager). The role ladder
now lives in CH Approval Authority (the single source of truth). This patch
preserves intent for existing rows:

  • approval_level == "Category Manager"  → routing_mode = "Category Manager"
  • everything else                       → routing_mode = "Approval Matrix"

Idempotent: only fills rows whose routing_mode is still empty.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "CH Exception Type"):
        return
    if not frappe.db.has_column("CH Exception Type", "routing_mode"):
        # New field not synced yet — migrate sync runs before patches normally,
        # but guard anyway so a partial run never errors.
        return

    has_legacy = frappe.db.has_column("CH Exception Type", "approval_level")

    if has_legacy:
        frappe.db.sql(
            """
            UPDATE `tabCH Exception Type`
            SET routing_mode = CASE
                WHEN approval_level = 'Category Manager' THEN 'Category Manager'
                ELSE 'Approval Matrix'
            END
            WHERE routing_mode IS NULL OR routing_mode = ''
            """
        )
    else:
        # Legacy column already gone — default any blanks to the matrix.
        frappe.db.sql(
            """
            UPDATE `tabCH Exception Type`
            SET routing_mode = 'Approval Matrix'
            WHERE routing_mode IS NULL OR routing_mode = ''
            """
        )

    frappe.db.commit()
