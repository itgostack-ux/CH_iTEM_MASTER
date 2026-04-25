# Copyright (c) 2026, GoStack and contributors
# DAT-3: CH Warranty Claim → CH Serial Lifecycle integration
#
# Registered in hooks.py doc_events["CH Warranty Claim"].
# Updates the serial lifecycle when a warranty claim is submitted or cancelled.

import frappe
from frappe import _
from frappe.utils import nowdate


def on_submit(doc, method=None):
    """Set serial lifecycle to 'In Service' when a warranty claim is approved and submitted.

    The device is now in GoGizmo's care for repair — reflect that in the
    serial lifecycle so reporting and dashboards stay accurate.
    """
    if not doc.serial_no:
        return
    if not frappe.db.exists("CH Serial Lifecycle", doc.serial_no):
        return

    current_status = frappe.db.get_value("CH Serial Lifecycle", doc.serial_no, "lifecycle_status")
    # Only advance from "Sold" or "Returned" states — don't overwrite in-progress service states
    if current_status not in ("Sold", "Returned", "Repaired"):
        return

    try:
        from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
            update_lifecycle_status,
        )
        update_lifecycle_status(
            serial_no=doc.serial_no,
            new_status="In Service",
            company=doc.get("company") or frappe.defaults.get_global_default("company"),
            warehouse=None,
            remarks=_("Warranty claim {0} submitted").format(doc.name),
            reference_doctype="CH Warranty Claim",
            reference_name=doc.name,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Warranty Claim → Serial Lifecycle sync failed for {doc.serial_no}",
        )


def on_cancel(doc, method=None):
    """Revert serial lifecycle to 'Sold' when a warranty claim is cancelled.

    Device is being returned to the customer without repair.
    """
    if not doc.serial_no:
        return
    if not frappe.db.exists("CH Serial Lifecycle", doc.serial_no):
        return

    current_status = frappe.db.get_value("CH Serial Lifecycle", doc.serial_no, "lifecycle_status")
    # Only revert if we're the ones who put it "In Service"
    if current_status != "In Service":
        return

    try:
        from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
            update_lifecycle_status,
        )
        update_lifecycle_status(
            serial_no=doc.serial_no,
            new_status="Sold",
            company=doc.get("company") or frappe.defaults.get_global_default("company"),
            warehouse=None,
            remarks=_("Warranty claim {0} cancelled — reverting status").format(doc.name),
            reference_doctype="CH Warranty Claim",
            reference_name=doc.name,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Warranty Claim cancel → Serial Lifecycle revert failed for {doc.serial_no}",
        )
