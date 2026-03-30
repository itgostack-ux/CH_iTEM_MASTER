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

import frappe
import requests
from frappe.utils import now_datetime


def _normalize_phone(phone: str) -> str:
    """Ensure phone has country code prefix (defaults to 91 for India)."""
    phone = (phone or "").strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if len(phone) == 10 and phone.isdigit():
        phone = "91" + phone
    return phone


def _get_settings():
    """Return cached CH WhatsApp Settings singleton."""
    try:
        return frappe.get_cached_doc("CH WhatsApp Settings")
    except frappe.DoesNotExistError:
        return None


def send_template_message(
    phone: str,
    template_name: str,
    body_values: dict | None = None,
    customer_name: str | None = None,
    ref_doctype: str | None = None,
    ref_name: str | None = None,
    enqueue: bool = True,
):
    """Send a WhatsApp template message via Gallabox.

    Args:
        phone: Recipient mobile number (Indian 10-digit or with country code).
        template_name: Gallabox template name.
        body_values: Dict of positional body values e.g. {"1": "John", "2": "SR-001"}.
        customer_name: Recipient display name.
        ref_doctype: Linked DocType for audit trail.
        ref_name: Linked document name for audit trail.
        enqueue: If True, runs via background job (default). Set False for
                 synchronous calls (e.g. OTP delivery where caller needs result).
    """
    if enqueue:
        frappe.enqueue(
            _send_now,
            queue="short",
            phone=phone,
            template_name=template_name,
            body_values=body_values,
            customer_name=customer_name,
            ref_doctype=ref_doctype,
            ref_name=ref_name,
        )
    else:
        _send_now(
            phone=phone,
            template_name=template_name,
            body_values=body_values,
            customer_name=customer_name,
            ref_doctype=ref_doctype,
            ref_name=ref_name,
        )


def _send_now(
    phone: str,
    template_name: str,
    body_values: dict | None = None,
    customer_name: str | None = None,
    ref_doctype: str | None = None,
    ref_name: str | None = None,
):
    """Actual HTTP call to Gallabox + audit log creation."""
    import json

    settings = _get_settings()
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

    log_doc = frappe.get_doc({
        "doctype": "CH WhatsApp Log",
        "recipient_phone": phone,
        "recipient_name": customer_name,
        "template_name": template_name,
        "body_values": json.dumps(body_values),
        "reference_doctype": ref_doctype,
        "reference_name": ref_name,
        "status": "Queued",
    })
    log_doc.insert(ignore_permissions=True)
    frappe.db.commit()

    try:
        resp = requests.post(
            base_url,
            json=payload,
            headers={
                "apiKey": api_key,
                "apiSecret": api_secret,
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        log_doc.db_set("status", "Sent", update_modified=True)
        log_doc.db_set("gallabox_response", json.dumps(resp.json()), update_modified=False)
    except Exception as e:
        frappe.log_error(
            title="WhatsApp Send Failed",
            message=f"Template: {template_name}, Phone: {phone}\n{str(e)}",
        )
        log_doc.db_set("status", "Failed", update_modified=True)
        log_doc.db_set("error_message", str(e)[:500], update_modified=False)
    finally:
        frappe.db.commit()
