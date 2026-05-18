# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Universal IMEI / Serial movement logger.

The CH Serial Lifecycle doctype already mirrors the *current* warehouse
of every serial via the ``Serial No.on_update`` hook. However, the
lifecycle_log audit table only receives a row when the **lifecycle_status**
changes (Received → In Stock → Sold, …).

That leaves a gap: pure location movements (Stock Entry between
warehouses, Stock Reconciliation, Delivery Note shipment, Transfer
Manifest pickup/delivery) move the device but do not transition its
lifecycle state — so the audit trail has nothing to show.

This module closes that gap. Each hook below appends a
``CH Serial Lifecycle Log`` row per affected serial whenever a stock
movement happens, with from_warehouse → to_warehouse and the source
document referenced in remarks. Status transitions are NOT performed
here (those continue to go through ``update_lifecycle_status`` so the
state machine stays authoritative).

Inserts are done directly via the ORM with ignore_permissions=True so
that warehouse-floor users can submit Stock Entries without needing
write access to CH Serial Lifecycle. Failures are swallowed and logged
so a movement-log failure never blocks the stock movement itself.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from ch_item_master.ch_item_master.serial_utils import get_serial_nos_from_item


# ── Core helper ────────────────────────────────────────────────────────────


def _append_log(
    serial_no: str,
    event: str,
    from_wh: str | None,
    to_wh: str | None,
    ref_dt: str | None,
    ref_dn: str | None,
    company: str | None = None,
    remarks_extra: str | None = None,
) -> None:
    """Insert one CH Serial Lifecycle Log row for a single serial.

    Safe: silently skips if the serial has no lifecycle row yet (the
    Serial No.on_update mirror will create one shortly). Never raises.
    """
    try:
        if not serial_no:
            return
        if not frappe.db.exists("CH Serial Lifecycle", serial_no):
            return

        current_status = frappe.db.get_value(
            "CH Serial Lifecycle", serial_no, "lifecycle_status"
        ) or ""

        # Build a human-readable remark
        bits: list[str] = [event]
        if from_wh and to_wh and from_wh != to_wh:
            bits.append(f"{from_wh} → {to_wh}")
        elif to_wh:
            bits.append(f"@ {to_wh}")
        if ref_dt and ref_dn:
            bits.append(f"[{ref_dt} {ref_dn}]")
        if remarks_extra:
            bits.append(remarks_extra)
        remarks = " · ".join(bits)

        log = frappe.get_doc({
            "doctype": "CH Serial Lifecycle Log",
            "parent": serial_no,
            "parenttype": "CH Serial Lifecycle",
            "parentfield": "lifecycle_log",
            "log_timestamp": now_datetime(),
            "from_status": current_status,
            "to_status": current_status,  # movement, not state change
            "changed_by": frappe.session.user,
            "company": company,
            "warehouse": to_wh or from_wh,
            "remarks": remarks[:140],
        })
        log.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(
            title="Serial movement log failed",
            message=frappe.get_traceback(),
        )


# ── Stock Entry ────────────────────────────────────────────────────────────


_SE_EVENT_BY_PURPOSE = {
    "Material Receipt": "Receipt",
    "Material Issue": "Issue",
    "Material Transfer": "Transfer",
    "Material Transfer for Manufacture": "Transfer (Mfg)",
    "Manufacture": "Manufacture",
    "Repack": "Repack",
    "Send to Subcontractor": "Send to Subcontractor",
    "Material Consumption for Manufacture": "Consumption",
}


def on_stock_entry_submit(doc, method=None) -> None:
    """Log every serialized movement on a submitted Stock Entry."""
    event = _SE_EVENT_BY_PURPOSE.get(getattr(doc, "purpose", ""), "Stock Entry")
    if getattr(doc, "add_to_transit", 0):
        event = f"{event} (In Transit)"
    for row in doc.get("items") or []:
        serials = get_serial_nos_from_item(row)
        if not serials:
            continue
        for sn in serials:
            _append_log(
                serial_no=sn,
                event=event,
                from_wh=row.get("s_warehouse"),
                to_wh=row.get("t_warehouse"),
                ref_dt="Stock Entry",
                ref_dn=doc.name,
                company=getattr(doc, "company", None),
            )


def on_stock_entry_cancel(doc, method=None) -> None:
    """Log SE cancellation as a reversal entry per serial."""
    event = _SE_EVENT_BY_PURPOSE.get(getattr(doc, "purpose", ""), "Stock Entry")
    event = f"{event} CANCELLED"
    for row in doc.get("items") or []:
        serials = get_serial_nos_from_item(row, parent_doc=doc)
        if not serials:
            continue
        for sn in serials:
            _append_log(
                serial_no=sn,
                event=event,
                from_wh=row.get("t_warehouse"),
                to_wh=row.get("s_warehouse"),
                ref_dt="Stock Entry",
                ref_dn=doc.name,
                company=getattr(doc, "company", None),
            )


# ── Delivery Note ──────────────────────────────────────────────────────────


def on_delivery_note_submit(doc, method=None) -> None:
    """Log each serial shipped out on a Delivery Note."""
    for row in doc.get("items") or []:
        serials = get_serial_nos_from_item(row)
        if not serials:
            continue
        for sn in serials:
            _append_log(
                serial_no=sn,
                event="Delivered",
                from_wh=row.get("warehouse"),
                to_wh=None,
                ref_dt="Delivery Note",
                ref_dn=doc.name,
                company=getattr(doc, "company", None),
                remarks_extra=f"to {doc.customer_name or doc.customer}" if doc.get("customer") else None,
            )


def on_delivery_note_cancel(doc, method=None) -> None:
    """Log DN cancellation per serial."""
    for row in doc.get("items") or []:
        serials = get_serial_nos_from_item(row, parent_doc=doc)
        if not serials:
            continue
        for sn in serials:
            _append_log(
                serial_no=sn,
                event="Delivery CANCELLED",
                from_wh=None,
                to_wh=row.get("warehouse"),
                ref_dt="Delivery Note",
                ref_dn=doc.name,
                company=getattr(doc, "company", None),
            )


# ── Stock Reconciliation ───────────────────────────────────────────────────


def on_stock_reconciliation_submit(doc, method=None) -> None:
    """Log each serial touched by a Stock Reconciliation."""
    for row in doc.get("items") or []:
        serials = get_serial_nos_from_item(row)
        if not serials:
            continue
        for sn in serials:
            _append_log(
                serial_no=sn,
                event="Reconciled",
                from_wh=None,
                to_wh=row.get("warehouse"),
                ref_dt="Stock Reconciliation",
                ref_dn=doc.name,
                company=getattr(doc, "company", None),
            )


# ── CH Transfer Manifest ───────────────────────────────────────────────────


_MANIFEST_STATUS_EVENT = {
    "Packed": "Manifest Packed",
    "Assigned": "Manifest Assigned to Courier",
    "Pickup Started": "Picked up by Courier",
    "In Transit": "In Transit",
    "Delivered": "Manifest Delivered",
    "Partially Received": "Partially Received",
    "Received": "Received at Destination",
    "Closed": "Manifest Closed",
    "Recall Initiated": "Manifest Recall Initiated",
    "Returned": "Manifest Returned",
    "Cancelled": "Manifest Cancelled",
}


def on_transfer_manifest_after_save(doc, method=None) -> None:
    """When manifest status changes, log it against every serial it carries.

    Pulls serials from each Stock Entry referenced by the manifest's
    transfers table. Idempotent per status change: uses doc._doc_before_save
    to detect transitions.
    """
    try:
        before = getattr(doc, "_doc_before_save", None)
        if before and before.status == doc.status:
            return
        event = _MANIFEST_STATUS_EVENT.get(doc.status)
        if not event:
            return

        # Collect all serials from linked Stock Entries
        for tr in doc.get("transfers") or []:
            se_name = tr.get("stock_entry")
            if not se_name:
                continue
            try:
                se = frappe.get_doc("Stock Entry", se_name)
            except Exception:
                continue
            for row in se.get("items") or []:
                serials = get_serial_nos_from_item(row)
                for sn in serials:
                    _append_log(
                        serial_no=sn,
                        event=event,
                        from_wh=tr.get("from_warehouse"),
                        to_wh=tr.get("to_warehouse"),
                        ref_dt="CH Transfer Manifest",
                        ref_dn=doc.name,
                        company=getattr(doc, "company", None),
                        remarks_extra=(
                            f"courier={doc.courier_partner}" if doc.get("courier_partner") else None
                        ),
                    )
    except Exception:
        frappe.log_error(
            title="Manifest movement log failed",
            message=frappe.get_traceback(),
        )
