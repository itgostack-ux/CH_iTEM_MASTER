"""Backfill CH State master from existing CH City.state values.

Also normalises any state strings on Customer to canonical CH State names.
Idempotent — safe to re-run.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "CH State") or not frappe.db.exists("DocType", "CH City"):
        return

    from ch_item_master.ch_core.doctype.ch_state.ch_state import ensure_state

    states = frappe.db.sql_list(
        "SELECT DISTINCT state FROM `tabCH City` WHERE state IS NOT NULL AND state != ''"
    )
    created = 0
    for s in states:
        canonical = ensure_state(s)
        if canonical and not frappe.db.exists("CH State", {"state_name": s.strip().title()}):
            created += 1

    frappe.db.commit()
    print(f"[CH State backfill] Ensured {len(states)} state(s), created {created} new")
