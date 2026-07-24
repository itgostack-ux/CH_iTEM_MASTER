"""Per-company SMS sending.

Mirrors the per-company WhatsApp design: a **CH SMS Account** holds each
company's gateway (URL, sender id, api key, parameter mapping). The resolver
falls back to Frappe's global **SMS Settings** so nothing breaks where no
per-company account exists.
"""
from urllib.parse import urlparse, urlunparse

import frappe
import requests
from frappe import _
from frappe.utils import cint

from ch_item_master.outbound_security import parse_exact_host_allowlist, validate_allowed_https_url


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
    from ch_item_master.ch_core.shadow_live import suppress_customer_comms

    if suppress_customer_comms():
        frappe.logger("shadow_live").info(f"SMS suppressed (shadow live) to={numbers}")
        return []

    if isinstance(numbers, str):
        numbers = [numbers]
    numbers = [str(n) for n in numbers if n]
    if not numbers:
        return []

    acct = get_sms_account(company)
    if acct and acct.sms_gateway_url:
        raw_allowed_hosts = acct.allowed_hosts
        timeout = max(1, min(cint(acct.gateway_timeout_seconds) or 10, 60))
    else:
        try:
            acct = frappe.get_cached_doc("SMS Settings")
            raw_allowed_hosts = frappe.db.get_single_value(
                "CH Item Master Settings", "sms_fallback_allowed_hosts"
            )
            timeout = max(
                1,
                min(
                    cint(
                        frappe.db.get_single_value(
                            "CH Item Master Settings", "sms_fallback_timeout_seconds"
                        )
                    )
                    or 10,
                    60,
                ),
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "send_company_sms: global SMS fallback failed")
            return []
        if not acct.sms_gateway_url:
            return []

    try:
        allowed_hosts = parse_exact_host_allowlist(raw_allowed_hosts, label="SMS Gateway")
        gateway_url = validate_allowed_https_url(
            acct.sms_gateway_url,
            allowed_hosts,
            label="SMS Gateway",
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Unsafe SMS gateway configuration for {company}")
        return []

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

    def _send_request(url: str) -> int:
        url = validate_allowed_https_url(url, allowed_hosts, label="SMS Gateway")
        kwargs = {
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": False,
            "stream": True,
        }
        if use_json:
            kwargs["json"] = args
        elif acct.use_post:
            kwargs["data"] = args
        else:
            kwargs["params"] = args
        response = requests.post(url, **kwargs) if acct.use_post else requests.get(url, **kwargs)
        try:
            if 300 <= response.status_code < 400:
                frappe.throw(_("SMS gateway redirects are not permitted."), frappe.ValidationError)
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 65536:
                frappe.throw(_("SMS gateway response exceeded the size limit."))
            return response.status_code
        finally:
            response.close()

    success = []
    for nbr in numbers:
        if acct.receiver_parameter:
            args[acct.receiver_parameter] = nbr
        try:
            status = _send_request(gateway_url)
            if 200 <= int(status) < 300:
                success.append(nbr)
        except Exception:
            fallback_url = _maybe_api_fallback(acct.sms_gateway_url)
            if fallback_url:
                try:
                    status = _send_request(fallback_url)
                    if 200 <= int(status) < 300:
                        success.append(nbr)
                        continue
                except Exception:
                    pass
            frappe.log_error(frappe.get_traceback(), f"send_company_sms failed for {nbr} ({company})")
    return success
