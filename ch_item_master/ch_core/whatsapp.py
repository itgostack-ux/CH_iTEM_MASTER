"""
Gallabox WhatsApp integration — central sending service.

Usage:
    from ch_item_master.ch_core.whatsapp import send_template_message

    send_template_message(
        phone="9876543210",
        template_name="gofix_device_received",
        body_values={"1": "John", "2": "SR-0001"},
        customer_name="John Doe",
        ref_doctype="Service Request",
        ref_name="SR-0001",
    )
"""

import re

import frappe
from frappe.utils import cint

from ch_item_master.outbound_security import parse_exact_host_allowlist, post_json_with_credentials


def _redact_otp_from_body_values(body_values: dict | None) -> dict | None:
    """Mask OTP codes in body_values before logging (H14 redaction).

    OTPs are typically 4-6 digit sequences. Redact them to prevent
    PII leakage to support staff reading logs.
    """
    if not body_values:
        return body_values
    redacted = {}
    otp_pattern = re.compile(r"\b\d{4,6}\b")  # 4-6 digit sequences
    for k, v in body_values.items():
        if isinstance(v, str) and otp_pattern.search(v):
            redacted[k] = "[REDACTED_OTP]"
        else:
            redacted[k] = v
    return redacted


def _normalize_phone(phone: str) -> str:
    """Ensure phone has country code prefix (defaults to 91 for India)."""
    phone = (phone or "").strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if len(phone) == 10 and phone.isdigit():
        phone = "91" + phone
    return phone


def get_whatsapp_settings(company: str | None = None):
    """Resolve the WhatsApp config to use.

    Per-company **CH WhatsApp Account** (credentials *and* template names) takes
    precedence; if a company has no enabled account we fall back to the global
    **CH WhatsApp Settings** single. Backward compatible — existing single-tenant
    sites keep working with no per-company setup.
    """
    if company:
        try:
            if frappe.db.exists("CH WhatsApp Account", company):
                acct = frappe.get_cached_doc("CH WhatsApp Account", company)
                if acct.enabled:
                    return acct
        except Exception:
            pass
    try:
        return frappe.get_cached_doc("CH WhatsApp Settings")
    except frappe.DoesNotExistError:
        return None


def _resolve_company(company, ref_doctype, ref_name):
    """Use the explicit company, else derive it from the referenced document
    (Buyback Order / Sales Invoice / Manifest … all carry `company`)."""
    if company:
        return company
    if ref_doctype and ref_name:
        try:
            if frappe.get_meta(ref_doctype).has_field("company"):
                return frappe.db.get_value(ref_doctype, ref_name, "company")
        except Exception:
            pass
    return None


def get_template(company: str | None, event: str | None):
    """Resolve (template_name, language) for an ops event.

    Order: the company's enabled **CH WhatsApp Template** mapped to the event →
    the **CH WhatsApp Event** catalog's `default_template` → (None, "en").
    Lets admins register any provider template and connect it to a trigger,
    instead of hardcoded fields.
    """
    if not event:
        return None, "en"
    if company:
        row = frappe.get_all(
            "CH WhatsApp Template",
            filters={"company": company, "event": event, "enabled": 1},
            fields=["template_name", "language"], limit=1,
        )
        if row:
            return row[0].template_name, (row[0].language or "en")
    default = None
    if frappe.db.exists("CH WhatsApp Event", event):
        default = frappe.db.get_value("CH WhatsApp Event", event, "default_template")
    return (default or None), "en"


def send_template_message(
    phone: str,
    template_name: str | None = None,
    body_values: dict | None = None,
    customer_name: str | None = None,
    ref_doctype: str | None = None,
    ref_name: str | None = None,
    enqueue: bool = True,
    company: str | None = None,
    event: str | None = None,
):
    """Send a WhatsApp template message via the company's provider (Gallabox).

    Args:
        phone: Recipient mobile number (Indian 10-digit or with country code).
        template_name: Explicit provider template. If omitted, resolved from `event`.
        body_values: Dict of positional body values e.g. {"1": "John", "2": "SR-001"}.
        customer_name: Recipient display name.
        ref_doctype: Linked DocType for audit trail.
        ref_name: Linked document name for audit trail.
        enqueue: If True, runs via background job (default). Set False for
                 synchronous calls (e.g. OTP delivery where caller needs result).
        company: Route via this company's WhatsApp account; auto-derived from
                 the referenced document when omitted.
        event: Ops trigger key (e.g. "buyback_otp"); resolves the template from
               the per-company library when `template_name` is not given.
    """
    from ch_item_master.ch_core.shadow_live import suppress_customer_comms

    if suppress_customer_comms():
        # Shadow-live pilot: never message customers. Log for the audit trail.
        frappe.logger("shadow_live").info(
            f"WhatsApp suppressed (shadow live): event={event or template_name} to={phone} ref={ref_doctype}/{ref_name}"
        )
        return {"suppressed": True, "reason": "shadow_live"}

    company = _resolve_company(company, ref_doctype, ref_name)
    if not template_name and event:
        template_name, _ = get_template(company, event)
    if not template_name:
        frappe.logger("whatsapp").info(
            f"WhatsApp: no template mapped for event={event} company={company}; skipping"
        )
        return
    if enqueue:
        # Deduplication: skip if the same message was enqueued for this doc within the last 10 minutes
        _phone_norm = _normalize_phone(phone or "")
        dedup_key = (
            f"wa_dedup_{ref_doctype or ''}_{ref_name or ''}_{template_name}_{_phone_norm}"
        )
        if frappe.cache().get_value(dedup_key):
            frappe.logger("whatsapp").info(
                f"WhatsApp dedup skip — {template_name} to {_phone_norm} "
                f"for {ref_doctype}/{ref_name} already enqueued within 10 min"
            )
            return
        frappe.cache().set_value(dedup_key, 1, expires_in_sec=600)
        frappe.enqueue(
            _send_now,
            queue="short",
            phone=phone,
            template_name=template_name,
            body_values=body_values,
            customer_name=customer_name,
            ref_doctype=ref_doctype,
            ref_name=ref_name,
            company=company,
            event=event,
        )
    else:
        _send_now(
            phone=phone,
            template_name=template_name,
            body_values=body_values,
            customer_name=customer_name,
            ref_doctype=ref_doctype,
            ref_name=ref_name,
            company=company,
            event=event,
        )


def _extract_message_id(resp_json) -> str | None:
    """Pull the provider message id from a send response (Gallabox/Meta shapes),
    so delivery-status webhooks can be matched back to the log row."""
    if not isinstance(resp_json, dict):
        return None
    for key in ("id", "messageId", "whatsappMessageId", "message_id", "wamid"):
        v = resp_json.get(key)
        if v:
            return str(v)
    # Meta Cloud API: {"messages": [{"id": "wamid..."}]}
    msgs = resp_json.get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict) and msgs[0].get("id"):
        return str(msgs[0]["id"])
    data = resp_json.get("data")
    if isinstance(data, dict):
        return _extract_message_id(data)
    return None


def _send_now(
    phone: str,
    template_name: str,
    body_values: dict | None = None,
    customer_name: str | None = None,
    ref_doctype: str | None = None,
    ref_name: str | None = None,
    company: str | None = None,
    event: str | None = None,
):
    """Actual HTTP call to Gallabox + audit log creation."""
    import json

    settings = get_whatsapp_settings(company)
    if not settings or not settings.enabled:
        frappe.logger("whatsapp").info(
            f"WhatsApp disabled — skipping {template_name} to {phone}"
        )
        return

    phone = _normalize_phone(phone)
    body_values = body_values or {}

    payload = {
        "channelId": settings.channel_id,
        "channelType": "whatsapp",
        "recipient": {
            "name": customer_name or "Customer",
            "phone": phone,
        },
        "whatsapp": {
            "type": "template",
            "template": {
                "templateName": template_name,
                "bodyValues": body_values,
            },
        },
    }

    api_key = settings.get_password("api_key") or ""
    api_secret = settings.get_password("api_secret") or ""
    base_url = settings.base_url or "https://server.gallabox.com/devapi/messages/whatsapp"
    allowed_hosts = parse_exact_host_allowlist(
        settings.get("allowed_hosts") or "server.gallabox.com",
        label="WhatsApp Gateway",
    )
    timeout = max(1, min(cint(settings.get("gateway_timeout_seconds")) or 30, 60))
    max_response_bytes = max(
        1024,
        min(cint(settings.get("gateway_response_max_bytes")) or 65536, 1048576),
    )

    log_doc = frappe.get_doc({
        "doctype": "CH WhatsApp Log",
        "recipient_phone": phone,
        "recipient_name": customer_name,
        "template_name": template_name,
        "company": company,
        "event": event,
        "provider": settings.get("provider") or "Gallabox",
        "body_values": json.dumps(_redact_otp_from_body_values(body_values)),
        "reference_doctype": ref_doctype,
        "reference_name": ref_name,
        "status": "Queued",
    })
    log_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    try:
        resp_json = post_json_with_credentials(
            base_url,
            allowed_hosts=allowed_hosts,
            label="WhatsApp Gateway",
            payload=payload,
            headers={
                "apiKey": api_key,
                "apiSecret": api_secret,
                "Content-Type": "application/json",
            },
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        log_doc.db_set("status", "Sent", update_modified=True)
        log_doc.db_set("gallabox_response", json.dumps(resp_json), update_modified=False)
        msg_id = _extract_message_id(resp_json)
        if msg_id:
            log_doc.db_set("provider_message_id", msg_id, update_modified=False)
    except Exception as e:
        frappe.log_error(
            title="WhatsApp Send Failed",
            message=f"Template: {template_name}, Phone: {phone}\n{str(e)}",
        )
        log_doc.db_set("status", "Failed", update_modified=True)
        log_doc.db_set("error_message", str(e)[:500], update_modified=False)
    finally:
        frappe.db.commit()
