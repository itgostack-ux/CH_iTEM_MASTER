"""Central message template registry (mirrors the legacy GoFix Template repo).

Templates are keyed by ``(code, channel, company)`` with a global fallback
when ``company`` is blank. Bodies use ``#PLACEHOLDER#`` tokens; token names
are matched case-insensitively against the supplied variables.

Typical use:

    from ch_item_master.ch_core.message_template import render_template

    msg = render_template(
        code="OTP",
        vars={"OTP": "123456", "MIN": "5", "CompanyName": "Example Company"},
        channel="SMS",
        company=current_company,
    )
    if msg:
        send_company_sms([mobile], msg["body"], company=current_company)
"""
from __future__ import annotations

import re
from typing import Iterable

import frappe

_TOKEN_RE = re.compile(r"#([A-Za-z0-9_]+)#")


def _lookup(code: str, channel: str, company: str | None):
    """Return the most specific enabled template, falling back to global."""
    if company:
        scoped = frappe.db.get_value(
            "CH Message Template",
            {"code": code, "channel": channel, "company": company, "enabled": 1},
            "name",
        )
        if scoped:
            return frappe.get_cached_doc("CH Message Template", scoped)
    # global fallback (company IS NULL or empty string)
    global_name = frappe.db.get_value(
        "CH Message Template",
        {"code": code, "channel": channel, "company": ["in", [None, ""]], "enabled": 1},
        "name",
    )
    if global_name:
        return frappe.get_cached_doc("CH Message Template", global_name)
    return None


def get_template(code: str, channel: str, company: str | None = None):
    """Return the resolved CH Message Template document, or ``None``."""
    if not code or not channel:
        return None
    try:
        return _lookup(code, channel, company)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "CH Message Template lookup failed")
        return None


def render_body(body: str, vars: dict | None) -> str:
    """Replace ``#KEY#`` tokens in ``body`` from ``vars`` (case-insensitive keys)."""
    if not body:
        return ""
    if not vars:
        return body
    lookup = {str(k).lower(): "" if v is None else str(v) for k, v in vars.items()}

    def _sub(match: re.Match) -> str:
        return lookup.get(match.group(1).lower(), match.group(0))

    return _TOKEN_RE.sub(_sub, body)


def render_template(
    code: str,
    vars: dict | None = None,
    channel: str = "SMS",
    company: str | None = None,
) -> dict | None:
    """Resolve a template by code and render it with the given variables.

    Returns ``{"code", "channel", "subject", "body", "language", "name"}`` or
    ``None`` when no matching enabled template exists.
    """
    doc = get_template(code, channel, company)
    if not doc:
        return None
    return {
        "code": doc.code,
        "channel": doc.channel,
        "subject": render_body(doc.subject or "", vars),
        "body": render_body(doc.body or "", vars),
        "language": doc.language or "en",
        "name": doc.name,
    }


def list_codes(channel: str | None = None, company: str | None = None) -> Iterable[str]:
    """Convenience helper for UIs that want to show available template codes."""
    filters: dict = {"enabled": 1}
    if channel:
        filters["channel"] = channel
    if company is not None:
        filters["company"] = company or ["in", [None, ""]]
    return frappe.get_all(
        "CH Message Template",
        filters=filters,
        pluck="code",
        order_by="code asc",
    )
