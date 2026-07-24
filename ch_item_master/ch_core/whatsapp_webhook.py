import base64
import hashlib
import hmac
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


def _find_log_by_message_id(message_id: str, company: str | None = None):
    if not message_id or not company:
        return None
    rows = frappe.get_all(
        "CH WhatsApp Log",
        filters={"provider_message_id": message_id, "company": company},
        fields=["name", "status"], order_by="creation desc", limit_page_length=1,
    )
    return rows[0] if rows else None


def _find_recent_log(phone: str, template_name: str, company: str | None = None):
    if not phone or not company:
        return None
    filters = {
        "recipient_phone": ["in", [phone, phone[-10:]]],
        "company": company,
    }
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


def _configured_webhook_credentials(company: str | None = None) -> list[dict]:
    company = (str(company).strip() if company else "") or None
    filters = {"enabled": 1}
    if company:
        filters["company"] = company
    credentials = []
    for row in frappe.get_all(
        "CH WhatsApp Account",
        filters=filters,
        fields=["name", "company"],
        order_by="name",
        limit_page_length=100,
    ):
        if not row.company or (company and row.company != company):
            continue
        account = frappe.get_cached_doc("CH WhatsApp Account", row.name)
        if not account.enabled or account.company != row.company:
            continue
        secret = account.get_password("webhook_secret") or ""
        token = account.get_password("webhook_verify_token") or ""
        if secret or token:
            credentials.append({
                "account": account.name,
                "company": account.company,
                "secret": secret,
                "token": token,
            })
    return credentials


def _valid_webhook_signature(raw_body: bytes, supplied_signature: str, secret: str) -> bool:
    if not raw_body or not supplied_signature or not secret:
        return False
    supplied = supplied_signature.strip()
    if supplied.lower().startswith("sha256="):
        supplied = supplied.split("=", 1)[1]
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(digest.hex(), supplied) or hmac.compare_digest(
        base64.b64encode(digest).decode("ascii"), supplied
    )


def _authenticate_webhook(company: str | None = None) -> str:
    company = (str(company).strip() if company else "") or None
    credentials = _configured_webhook_credentials(company)
    if not credentials:
        frappe.throw("WhatsApp webhook credentials are not configured", frappe.AuthenticationError)

    if frappe.request.method == "GET":
        supplied = (
            frappe.form_dict.get("hub.verify_token")
            or frappe.form_dict.get("verify_token")
            or ""
        )
        matches = [
            item for item in credentials
            if item["token"] and hmac.compare_digest(item["token"], supplied)
        ]
    else:
        raw_body = frappe.request.get_data(cache=True) or b""
        signature = (
            frappe.get_request_header("X-Gallabox-Signature")
            or frappe.get_request_header("X-Webhook-Signature")
            or frappe.get_request_header("X-Hub-Signature-256")
            or ""
        )
        matches = [
            item for item in credentials
            if _valid_webhook_signature(raw_body, signature, item["secret"])
        ]

    matched_companies = {item["company"] for item in matches if item.get("company")}
    if len(matched_companies) != 1:
        frappe.throw(
            "WhatsApp webhook authentication is invalid or ambiguous",
            frappe.AuthenticationError,
        )
    resolved_company = next(iter(matched_companies))
    if company and resolved_company != company:
        frappe.throw("WhatsApp webhook company mismatch", frappe.AuthenticationError)
    return resolved_company


def _extract_payload_company(payload: dict) -> str | None:
    value = payload.get("company")
    if not value and isinstance(payload.get("metadata"), dict):
        value = payload["metadata"].get("company")
    value = str(value or "").strip()
    return value or None


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=600, seconds=60, ip_based=True)
def gallabox_webhook(company=None):
    company = (str(company).strip() if company else "") or None
    resolved_company = _authenticate_webhook(company)
    payload = _request_payload()
    if frappe.request.method == "GET":
        return payload.get("challenge") or ""
    payload_company = _extract_payload_company(payload)
    if payload_company and payload_company != resolved_company:
        frappe.throw("WhatsApp webhook payload company mismatch", frappe.AuthenticationError)
    return process_status_event(payload, company=resolved_company)


def process_status_event(payload: dict, company: str | None = None) -> dict:
    """Apply a provider status/inbound webhook to the CH WhatsApp Log.
    Pure function (no request access) so it is unit-testable."""
    company = (str(company).strip() if company else "") or None
    if not company:
        frappe.throw("A resolved webhook company is required", frappe.AuthenticationError)
    phone = _extract_phone(payload)
    template_name = _extract_template(payload)
    message_id = _extract_message_id(payload)
    delivery_status = _extract_delivery_status(payload)
    mapped_status = STATUS_MAP.get(
        delivery_status, "Failed" if delivery_status in FAILED_STATUSES else "Sent"
    )

    # Match by provider message id first (reliable), then fall back to phone+template.
    existing = _find_log_by_message_id(message_id, company) or _find_recent_log(phone, template_name, company)
    if existing:
        locked_name = frappe.db.get_value(
            "CH WhatsApp Log",
            {"name": existing["name"], "company": company},
            "name",
            for_update=True,
        )
        if not locked_name:
            frappe.throw("WhatsApp log company mismatch", frappe.AuthenticationError)
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
                "company": company,
                "status": mapped_status,
                "body_values": json.dumps({"event": delivery_status, "direction": "inbound"}),
                "gallabox_response": json.dumps(payload),
                "error_message": json.dumps(payload)[:500] if mapped_status == "Failed" else "",
            }
        )
        log_doc.insert(ignore_permissions=True)

    return {"ok": True, "status": mapped_status, "message_id": message_id,
            "template_name": template_name, "phone": phone}
