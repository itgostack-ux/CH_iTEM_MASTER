import frappe
from frappe import _

from ch_item_master.config import get_int_setting, require_role_setting


_LOGISTICS_ROLES = ("CH Warranty Manager", "Service Manager", "Sales Manager")
_QUEUE_STATUSES = frozenset(
    {"Pickup Requested", "Pickup Scheduled", "Picked Up", "Device Received"}
)


def _require_logistics_access(action):
    require_role_setting("warranty_claim_logistics_roles", _LOGISTICS_ROLES, action=action)
    frappe.has_permission("CH Warranty Claim", "read", throw=True)


@frappe.whitelist()
def get_logistics_queue(filters=None):
    """Return online/phone claims in pickup-stage for logistics team.

    Only claims with claim_channel IN ('Online/Bot', 'Phone') and in a
    pickup-stage status are shown here. Store/walk-in claims are NOT shown.
    """
    _require_logistics_access(_("view the warranty logistics queue"))
    filters = filters or {}
    if isinstance(filters, str):
        try:
            filters = frappe.parse_json(filters) or {}
        except Exception:
            frappe.throw(_("Invalid logistics queue filters."))
    if not isinstance(filters, dict):
        frappe.throw(_("Invalid logistics queue filters."))

    status_filter = filters.get("status") or sorted(_QUEUE_STATUSES)
    if isinstance(status_filter, str):
        status_filter = [status_filter]
    status_filter = [status for status in status_filter if status in _QUEUE_STATUSES]
    if not status_filter:
        return []

    try:
        from ch_erp15.ch_erp15.scope import intersect_filters
    except (ImportError, ModuleNotFoundError):
        frappe.throw(_("Location scope validation is unavailable."), frappe.PermissionError)

    effective = intersect_filters(
        company=filters.get("company"),
        city=filters.get("city"),
        zone=filters.get("zone"),
        store=filters.get("store"),
    )

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
    if effective.get("company"):
        cond_parts.append("c.company = %(company)s")
        params["company"] = effective["company"]
    allowed_stores = effective.get("allowed_stores")
    if allowed_stores is not None:
        if not allowed_stores:
            return []
        cond_parts.append("c.reported_at_store IN %(stores)s")
        params["stores"] = tuple(allowed_stores)

    limit = min(get_int_setting("warranty_logistics_queue_limit", 200, minimum=1), 1000)
    params["limit"] = limit

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
        + " ORDER BY c.creation ASC LIMIT %(limit)s",
        params,
        as_dict=True,
    )
    return rows


@frappe.whitelist(methods=["POST"])
def schedule_pickup(claim_name, pickup_slot, pickup_partner=None, pickup_tracking_no=None):
    """Schedule pickup for an online/bot claim."""
    doc = frappe.get_doc("CH Warranty Claim", claim_name)
    return doc.schedule_pickup(
        pickup_slot=pickup_slot,
        pickup_partner=pickup_partner,
        pickup_tracking_no=pickup_tracking_no,
    )


@frappe.whitelist(methods=["POST"])
def mark_picked_up(claim_name, delivery_otp=None, remarks=None):
    """Mark device as picked up — enforces photo gate for online claims."""
    doc = frappe.get_doc("CH Warranty Claim", claim_name)
    return doc.mark_picked_up(delivery_otp=delivery_otp, remarks=remarks)


@frappe.whitelist(methods=["POST"])
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
