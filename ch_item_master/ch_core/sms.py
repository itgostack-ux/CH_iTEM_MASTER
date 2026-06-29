"""Per-company SMS sending.

Mirrors the per-company WhatsApp design: a **CH SMS Account** holds each
company's gateway (URL, sender id, api key, parameter mapping). The resolver
falls back to Frappe's global **SMS Settings** so nothing breaks where no
per-company account exists.
"""
from urllib.parse import urlparse, urlunparse

import frappe


def get_sms_account(company: str | None = None):
    """Return the company's enabled CH SMS Account, else None (caller falls back)."""
    if company:
        try:
            if frappe.db.exists("CH SMS Account", company):
                acct = frappe.get_cached_doc("CH SMS Account", company)
                if acct.enabled:
                    return acct
        except Exception:
            pass
    return None


def get_otp_expiry(company: str | None = None, default: int = 5) -> int:
    acct = get_sms_account(company)
    if acct and acct.otp_expiry:
        return int(acct.otp_expiry)
    return default


def send_company_sms(numbers, message: str, company: str | None = None, sender_name: str = ""):
    """Send an SMS via the company's gateway; fall back to global SMS Settings.

    Best-effort: never raises to the caller (OTP/notification flows continue).
    Returns the list of numbers accepted by the gateway.
    """
    if isinstance(numbers, str):
        numbers = [numbers]
    numbers = [str(n) for n in numbers if n]
    if not numbers:
        return []

    acct = get_sms_account(company)
    if not acct or not acct.sms_gateway_url:
        # Global fallback — Frappe SMS Settings.
        try:
            from frappe.core.doctype.sms_settings.sms_settings import send_sms
            return send_sms(numbers, message, sender_name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "send_company_sms: global SMS fallback failed")
            return []

    from frappe.core.doctype.sms_settings.sms_settings import send_request

    # Build args + headers from the account's parameter mapping (same model as
    # Frappe SMS Settings: static rows flagged `header` go to headers).
    args = {}
    if acct.message_parameter:
        args[acct.message_parameter] = message
    headers = {"Accept": "text/plain, text/html, */*"}
    for d in (acct.get("parameters") or []):
        if getattr(d, "header", 0):
            headers[d.parameter] = d.value
        else:
            args[d.parameter] = d.value
    use_json = headers.get("Content-Type") == "application/json"

    def _maybe_api_fallback(url: str) -> str | None:
        """For mtalkz root URLs, retry once on /api (matches many existing C# clients)."""
        provider = (getattr(acct, "provider", "") or "").strip().lower()
        parsed = urlparse(url or "")
        if provider != "mtalkz" or not parsed.netloc:
            return None
        if parsed.path and parsed.path not in ("", "/"):
            return None
        return urlunparse((parsed.scheme or "https", parsed.netloc, "/api", "", "", ""))

    success = []
    for nbr in numbers:
        if acct.receiver_parameter:
            args[acct.receiver_parameter] = nbr
        try:
            status = send_request(acct.sms_gateway_url, args, headers, acct.use_post, use_json)
            if 200 <= int(status) < 300:
                success.append(nbr)
        except Exception:
            fallback_url = _maybe_api_fallback(acct.sms_gateway_url)
            if fallback_url:
                try:
                    status = send_request(fallback_url, args, headers, acct.use_post, use_json)
                    if 200 <= int(status) < 300:
                        success.append(nbr)
                        continue
                except Exception:
                    pass
            frappe.log_error(frappe.get_traceback(), f"send_company_sms failed for {nbr} ({company})")
    return success
