import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime


@frappe.whitelist()
def get_logistics_queue(filters=None):
    """Return online/phone claims in pickup-stage for logistics team.

    Only claims with claim_channel IN ('Online/Bot', 'Phone') and in a
    pickup-stage status are shown here. Store/walk-in claims are NOT shown.
    """
    filters = filters or {}
    if isinstance(filters, str):
        try:
            filters = frappe.parse_json(filters) or {}
        except Exception:
            filters = {}

    status_filter = filters.get("status") or [
        "Pickup Requested", "Pickup Scheduled", "Picked Up", "Device Received",
    ]
    if isinstance(status_filter, str):
        status_filter = [status_filter]

    channel_filter = ["Online/Bot", "Phone"]

    cond_parts = [
        "c.docstatus = 1",
        "c.claim_channel IN %(channels)s",
        "c.claim_status IN %(statuses)s",
    ]
    params = {
        "channels": tuple(channel_filter),
        "statuses": tuple(status_filter),
    }
    if filters.get("company"):
        cond_parts.append("c.company = %(company)s")
        params["company"] = filters["company"]

    rows = frappe.db.sql(
        """
        SELECT
            c.name, c.claim_id, c.claim_status, c.claim_channel,
            c.customer, c.customer_name, c.customer_phone, c.customer_email,
            c.serial_no, c.item_code, c.brand,
            c.pickup_address, c.pickup_slot, c.pickup_partner,
            c.pickup_tracking_no, c.picked_up_at, c.device_received_at,
            c.parcel_photo_1, c.parcel_photo_2,
            c.device_received_photo_1, c.device_received_photo_2,
            c.logistics_status, c.repair_destination,
            c.creation, c.modified
        FROM `tabCH Warranty Claim` c
        WHERE """
        + " AND ".join(cond_parts)
        + " ORDER BY c.creation ASC",
        params,
        as_dict=True,
    )
    return rows


@frappe.whitelist()
def schedule_pickup(claim_name, pickup_slot, pickup_partner=None, pickup_tracking_no=None):
    """Schedule pickup for an online/bot claim."""
    frappe.has_permission("CH Warranty Claim", "write", throw=True)
    doc = frappe.get_doc("CH Warranty Claim", claim_name)
    if doc.claim_status not in ("Pickup Requested", "Approved"):
        frappe.throw(_("Claim is not in a state where pickup can be scheduled."))
    updates = {
        "pickup_slot": pickup_slot,
        "logistics_status": "Pickup Scheduled",
        "claim_status": "Pickup Scheduled",
    }
    if pickup_partner:
        updates["pickup_partner"] = pickup_partner
    if pickup_tracking_no:
        updates["pickup_tracking_no"] = pickup_tracking_no
    doc.db_set(updates)
    doc._log(
        "Pickup Scheduled",
        "Pickup Requested",
        "Pickup Scheduled",
        f"Pickup scheduled for {pickup_slot}. Partner: {pickup_partner or '—'}",
    )
    return {"status": "Pickup Scheduled"}


@frappe.whitelist()
def mark_picked_up(claim_name, delivery_otp=None, remarks=None):
    """Mark device as picked up — enforces photo gate for online claims."""
    doc = frappe.get_doc("CH Warranty Claim", claim_name)
    return doc.mark_picked_up(delivery_otp=delivery_otp, remarks=remarks)


@frappe.whitelist()
def mark_device_received(
    claim_name,
    condition_on_receipt,
    accessories_received=None,
    imei_verified=None,
    receiving_remarks=None,
):
    """Mark device as received at hub after pickup."""
    doc = frappe.get_doc("CH Warranty Claim", claim_name)
    return doc.mark_device_received(
        condition_on_receipt=condition_on_receipt,
        accessories_received=accessories_received,
        imei_verified=imei_verified,
        receiving_remarks=receiving_remarks,
    )
