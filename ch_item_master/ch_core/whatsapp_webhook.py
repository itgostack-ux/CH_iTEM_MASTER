import json

import frappe

from ch_item_master.ch_core.whatsapp import _normalize_phone

FAILED_STATUSES = {"failed", "error", "undelivered", "rejected"}


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
def gallabox_webhook():
    payload = _request_payload()
    if payload.get("challenge"):
        return payload["challenge"]

    phone = _extract_phone(payload)
    template_name = _extract_template(payload)
    delivery_status = _extract_delivery_status(payload)
    mapped_status = "Failed" if delivery_status in FAILED_STATUSES else "Sent"

    existing = _find_recent_log(phone, template_name)
    if existing:
        frappe.db.set_value("CH WhatsApp Log", existing["name"], "status", mapped_status, update_modified=True)
        frappe.db.set_value(
            "CH WhatsApp Log",
            existing["name"],
            "gallabox_response",
            json.dumps(payload),
            update_modified=False,
        )
        if mapped_status == "Failed":
            frappe.db.set_value(
                "CH WhatsApp Log",
                existing["name"],
                "error_message",
                json.dumps(payload)[:500],
                update_modified=False,
            )
    else:
        log_doc = frappe.get_doc(
            {
                "doctype": "CH WhatsApp Log",
                "recipient_phone": phone,
                "template_name": template_name or f"webhook:{delivery_status}",
                "status": mapped_status,
                "body_values": json.dumps({"event": delivery_status, "direction": "inbound"}),
                "gallabox_response": json.dumps(payload),
                "error_message": json.dumps(payload)[:500] if mapped_status == "Failed" else "",
            }
        )
        log_doc.insert(ignore_permissions=True)

    frappe.db.commit()
    return {"ok": True, "status": mapped_status, "template_name": template_name, "phone": phone}
