# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Break Glass Audit — Script Report

Enterprise-grade emergency-access monitoring view, modelled on
SAP GRC Access Control "Firefighter Log Report" and
Oracle Fusion "Privileged Access Audit".

Shows every break-glass session (active or closed) within the chosen
window with SLA aging buckets so System Managers / Auditors can quickly
spot sessions that have stayed open past the configured thresholds.

Filters
    from_date, to_date         — start window (defaults to last 30 days)
    user                       — filter by the actor who opened the session
    status                     — Open / Closed / All
    review_status              — Pending Review / Reviewed / Escalated / All
    sla_breach                 — show only rows breaching the SLA threshold
    sla_hours                  — SLA target (default 4h) for the "Aging" bucket
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime, get_datetime, getdate

from ch_item_master.config import get_int_setting

# SLA buckets (hours). Aligned with SAP GRC defaults:
#   Within Target ≤ SLA target (default 4h)
#   Breach      → SLA target < age ≤ Hard limit (default 24h)
#   Hard Breach → age > Hard limit
DEFAULT_SLA_HOURS = 4


def execute(filters=None):
    filters = _resolve_filters(filters)
    columns = _get_columns()
    data = _get_data(filters)
    summary = _get_summary(data, filters)
    chart = _get_chart(data)
    return columns, data, None, chart, summary


# ─────────────────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_filters(filters: dict | None) -> dict:
    filters = dict(filters or {})
    filters.setdefault("from_date", add_days(getdate(), -30))
    filters.setdefault("to_date", getdate())
    filters.setdefault("status", "All")
    filters.setdefault("review_status", "All")
    filters.setdefault("sla_hours", DEFAULT_SLA_HOURS)
    filters["sla_hours"] = max(1, int(filters.get("sla_hours") or DEFAULT_SLA_HOURS))
    filters["hard_limit_hours"] = get_int_setting("break_glass_hard_limit_hours", 24, 1)
    return filters


# ─────────────────────────────────────────────────────────────────────────────
# Columns
# ─────────────────────────────────────────────────────────────────────────────

def _get_columns() -> list[dict]:
    return [
        {"fieldname": "name", "label": _("Log"), "fieldtype": "Link",
         "options": "CH Break Glass Log", "width": 130},
        {"fieldname": "user", "label": _("User"), "fieldtype": "Link",
         "options": "User", "width": 200},
        {"fieldname": "reason", "label": _("Reason"), "fieldtype": "Data", "width": 280},
        {"fieldname": "start_time", "label": _("Started"), "fieldtype": "Datetime", "width": 150},
        {"fieldname": "end_time", "label": _("Closed"), "fieldtype": "Datetime", "width": 150},
        {"fieldname": "duration_hours", "label": _("Duration (h)"), "fieldtype": "Float",
         "precision": 2, "width": 110},
        {"fieldname": "status", "label": _("Session"), "fieldtype": "Data", "width": 90},
        {"fieldname": "sla_bucket", "label": _("SLA Aging"), "fieldtype": "Data", "width": 130},
        {"fieldname": "review_status", "label": _("Review"), "fieldtype": "Data", "width": 130},
        {"fieldname": "reviewed_by", "label": _("Reviewed By"), "fieldtype": "Link",
         "options": "User", "width": 180},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Query + aging classification
# ─────────────────────────────────────────────────────────────────────────────

def _get_data(filters: dict) -> list[dict]:
    conditions = ["bgl.start_time >= %(from_date)s", "bgl.start_time <= %(to_date_end)s"]
    values = {
        "from_date": getdate(filters["from_date"]),
        "to_date_end": f"{getdate(filters['to_date'])} 23:59:59",
    }

    if filters.get("user"):
        conditions.append("bgl.user = %(user)s")
        values["user"] = filters["user"]

    if filters.get("status") == "Open":
        conditions.append("bgl.end_time IS NULL")
    elif filters.get("status") == "Closed":
        conditions.append("bgl.end_time IS NOT NULL")

    if filters.get("review_status") and filters["review_status"] != "All":
        conditions.append("bgl.review_status = %(review_status)s")
        values["review_status"] = filters["review_status"]

    rows = frappe.db.sql(
        """
        SELECT
            bgl.name,
            bgl.user,
            bgl.reason,
            bgl.start_time,
            bgl.end_time,
            bgl.duration_hours,
            bgl.review_status,
            bgl.reviewed_by
        FROM `tabCH Break Glass Log` bgl
        WHERE {where}
        ORDER BY bgl.start_time DESC
        """.format(where=" AND ".join(conditions)),
        values,
        as_dict=True,
    )

    sla_hours = int(filters["sla_hours"])
    now = now_datetime()
    enriched: list[dict] = []
    for r in rows:
        start = get_datetime(r.start_time) if r.start_time else None
        end = get_datetime(r.end_time) if r.end_time else None
        is_open = end is None
        ref_time = end or now
        hours = (ref_time - start).total_seconds() / 3600 if start else 0

        # Recompute duration when it was not persisted (e.g., still open).
        r["duration_hours"] = round(hours, 2)
        r["status"] = "Open" if is_open else "Closed"
        r["sla_bucket"] = _classify(hours, sla_hours, filters["hard_limit_hours"])
        r["reason"] = (r.get("reason") or "").strip().replace("\n", " ")[:280]
        # Optional row indicator picked up by Frappe report renderer.
        r["indicator"] = _indicator(r["sla_bucket"])

        if filters.get("sla_breach") and r["sla_bucket"] == "Within Target":
            continue
        enriched.append(r)
    return enriched


def _classify(hours: float, sla_hours: int, hard_limit: int) -> str:
    if hours <= sla_hours:
        return "Within Target"
    if hours <= hard_limit:
        return "Breach"
    return "Hard Breach"


def _indicator(bucket: str) -> str:
    return {
        "Within Target": "green",
        "Breach": "orange",
        "Hard Breach": "red",
    }.get(bucket, "grey")


# ─────────────────────────────────────────────────────────────────────────────
# Summary cards + chart
# ─────────────────────────────────────────────────────────────────────────────

def _get_summary(rows: list[dict], filters: dict) -> list[dict]:
    total = len(rows)
    open_count = sum(1 for r in rows if r["status"] == "Open")
    breach = sum(1 for r in rows if r["sla_bucket"] == "Breach")
    hard = sum(1 for r in rows if r["sla_bucket"] == "Hard Breach")
    pending = sum(1 for r in rows if r.get("review_status") == "Pending Review")
    return [
        {"label": _("Total Sessions"), "value": total, "indicator": "Blue"},
        {"label": _("Currently Open"), "value": open_count,
         "indicator": "Red" if open_count else "Green"},
        {"label": _("SLA Breach (>{0}h)").format(filters["sla_hours"]),
         "value": breach, "indicator": "Orange" if breach else "Green"},
        {"label": _("Hard Breach (>{0}h)").format(filters["hard_limit_hours"]),
         "value": hard, "indicator": "Red" if hard else "Green"},
        {"label": _("Pending Review"), "value": pending,
         "indicator": "Orange" if pending else "Green"},
    ]


def _get_chart(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    buckets = {"Within Target": 0, "Breach": 0, "Hard Breach": 0}
    for r in rows:
        buckets[r["sla_bucket"]] = buckets.get(r["sla_bucket"], 0) + 1
    return {
        "type": "donut",
        "data": {
            "labels": list(buckets.keys()),
            "datasets": [{"values": list(buckets.values())}],
        },
        "colors": ["#22c55e", "#f59e0b", "#ef4444"],
    }
