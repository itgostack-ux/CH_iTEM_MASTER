import json

import frappe
from frappe.rate_limiter import rate_limit

from ch_item_master.ch_core.whatsapp import _normalize_phone

FAILED_STATUSES = {"failed", "error", "undelivered", "rejected"}

# Map provider delivery-status strings to CH WhatsApp Log lifecycle states.
STATUS_MAP = {
    "sent": "Sent", "submitted": "Sent", "accepted": "Sent",
    "delivered": "Delivered",
    "read": "Read", "seen": "Read",
    "failed": "Failed", "error": "Failed", "undelivered": "Failed", "rejected": "Failed",
}
# Rank so a late/out-of-order webhook never regresses a more advanced state.
STATUS_RANK = {"Queued": 0, "Sent": 1, "Delivered": 2, "Read": 3, "Failed": 1}


def _request_payload() -> dict:
    if getattr(frappe, "request", None) and frappe.request.method == "GET":
        challenge = frappe.form_dict.get("challenge") or frappe.form_dict.get("hub.challenge")
        return {"challenge": challenge} if challenge else {}

    payload = {}
    if getattr(frappe, "request", None):
        payload = frappe.request.get_json(silent=True) or {}
        if not payload:
            raw_data = frappe.request.get_data(as_text=True) or "{}"
            payload = json.loads(raw_data)
    if isinstance(payload, list):
        payload = {"events": payload}
    return payload


def _extract_phone(payload: dict) -> str:
    phone = payload.get("phone") or payload.get("recipient_phone") or payload.get("mobile") or payload.get("mobile_no")
    if not phone and isinstance(payload.get("recipient"), dict):
        phone = payload["recipient"].get("phone") or payload["recipient"].get("mobile")
    if not phone and isinstance(payload.get("contact"), dict):
        phone = payload["contact"].get("phone") or payload["contact"].get("mobile")
    return _normalize_phone(str(phone or ""))


def _extract_template(payload: dict) -> str:
    template = payload.get("template_name") or payload.get("template") or payload.get("event_type") or payload.get("type") or ""
    if isinstance(template, dict):
        template = template.get("name") or template.get("templateName") or ""
    whatsapp = payload.get("whatsapp") or {}
    if not template and isinstance(whatsapp, dict):
        template = ((whatsapp.get("template") or {}).get("templateName")) or whatsapp.get("type") or ""
    return str(template or "").strip()


def _extract_delivery_status(payload: dict) -> str:
    for key in ("delivery_status", "status", "event_type", "type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return "received"


def _extract_message_id(payload: dict) -> str:
    """Pull the provider message id from a status webhook (Gallabox/Meta shapes)."""
    for key in ("whatsappMessageId", "messageId", "message_id", "id", "wamid"):
        v = payload.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    for nest in ("whatsapp", "message", "data", "statuses", "entry"):
        v = payload.get(nest)
        if isinstance(v, dict):
            got = _extract_message_id(v)
            if got:
                return got
        if isinstance(v, list) and v and isinstance(v[0], dict):
            got = _extract_message_id(v[0])
            if got:
                return got
    return ""


def _find_log_by_message_id(message_id: str):
    if not message_id:
        return None
    rows = frappe.get_all(
        "CH WhatsApp Log",
        filters={"provider_message_id": message_id},
        fields=["name", "status"], order_by="creation desc", limit_page_length=1,
    )
    return rows[0] if rows else None


def _find_recent_log(phone: str, template_name: str):
    if not phone:
        return None
    filters = {"recipient_phone": ["in", [phone, phone[-10:]]]}
    if template_name:
        filters["template_name"] = template_name
    rows = frappe.get_all(
        "CH WhatsApp Log",
        filters=filters,
        fields=["name", "status"],
        order_by="creation desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=600, seconds=60, ip_based=True)
def gallabox_webhook():
    payload = _request_payload()
    if payload.get("challenge"):
        return payload["challenge"]
    return process_status_event(payload)


def process_status_event(payload: dict) -> dict:
    """Apply a provider status/inbound webhook to the CH WhatsApp Log.
    Pure function (no request access) so it is unit-testable."""
    phone = _extract_phone(payload)
    template_name = _extract_template(payload)
    message_id = _extract_message_id(payload)
    delivery_status = _extract_delivery_status(payload)
    mapped_status = STATUS_MAP.get(
        delivery_status, "Failed" if delivery_status in FAILED_STATUSES else "Sent"
    )

    # Match by provider message id first (reliable), then fall back to phone+template.
    existing = _find_log_by_message_id(message_id) or _find_recent_log(phone, template_name)
    if existing:
        updates = {"gallabox_response": json.dumps(payload)}
        # Never regress a more-advanced lifecycle state (out-of-order webhooks).
        cur_rank = STATUS_RANK.get(existing.get("status"), 0)
        if mapped_status == "Failed" or STATUS_RANK.get(mapped_status, 0) >= cur_rank:
            updates["status"] = mapped_status
        if mapped_status == "Delivered":
            updates["delivered_at"] = frappe.utils.now_datetime()
        elif mapped_status == "Read":
            # a read implies delivered; backfill if the delivered webhook was missed
            updates["read_at"] = frappe.utils.now_datetime()
        elif mapped_status == "Failed":
            updates["error_message"] = json.dumps(payload)[:500]
        frappe.db.set_value("CH WhatsApp Log", existing["name"], updates, update_modified=True)
    else:
        log_doc = frappe.get_doc(
            {
                "doctype": "CH WhatsApp Log",
                "recipient_phone": phone,
                "template_name": template_name or f"webhook:{delivery_status}",
                "provider_message_id": message_id,
                "status": mapped_status,
                "body_values": json.dumps({"event": delivery_status, "direction": "inbound"}),
                "gallabox_response": json.dumps(payload),
                "error_message": json.dumps(payload)[:500] if mapped_status == "Failed" else "",
            }
        )
        log_doc.insert(ignore_permissions=True)

    frappe.db.commit()
    return {"ok": True, "status": mapped_status, "message_id": message_id,
            "template_name": template_name, "phone": phone}
