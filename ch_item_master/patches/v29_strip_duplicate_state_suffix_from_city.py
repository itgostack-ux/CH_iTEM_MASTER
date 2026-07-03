"""v29 — Repair CH City rows accidentally double-suffixed with state code.

Background
----------
``CHCity.autoname`` appends ``-{state_code}`` to build a deterministic PK
(``Chennai-33``, ``Bilaspur-CG``). A subset of legacy sites carry rows
where the ``city_name`` FIELD itself was set to the already-suffixed value
(e.g. ``city_name = "Chennai-33"``). When autoname ran on those rows it
re-appended the state code, producing PKs like ``Chennai-33-33`` and
``Madurai-33-33``. Those wrong PKs then propagated to Link fields on
CH Store, CH Store Zone, Warehouse.ch_city, etc., which makes v27 seed
imports fail with ``Warehouse … already serves zone … in city
Chennai-33-33`` errors during migrate.

Fix
---
For every CH City row whose ``city_name`` ends with ``-{state_code}``
(for that row's state), strip the accidental suffix from the field value
and rename the doc to the canonical PK. If the canonical PK already
exists (a clean twin), MERGE the double-suffixed row into it —
``frappe.rename_doc`` re-points every downstream Link automatically.

Idempotent: re-running on already-clean data is a no-op. Legitimate
hyphenated district names (``Janjgir-Champa``, ``Medchal-Malkajgiri``,
``Gaurela-Pendra-Marwahi``) are NOT touched because their ``city_name``
does not end with a state code token.
"""

from __future__ import annotations

import frappe


def _state_code(state: str | None) -> str | None:
    if not state:
        return None
    code = (frappe.db.get_value("CH State", state, "state_code") or "").strip().upper()
    if code:
        return code
    return "".join(ch for ch in state.upper() if ch.isalnum()) or None


def _strip_suffix(city_name: str, state_token: str | None) -> str:
    if not city_name or not state_token:
        return city_name
    suffix = f"-{state_token}"
    if city_name.upper().endswith(suffix.upper()):
        stripped = city_name[: -len(suffix)].strip()
        if stripped:
            return stripped
    return city_name


def execute():
    if not frappe.db.table_exists("CH City"):
        return

    rows = frappe.get_all(
        "CH City",
        fields=["name", "city_name", "state"],
        limit_page_length=0,
    )

    stripped_field = renamed = merged = unchanged = failed = 0

    for row in rows:
        state_token = _state_code(row.state)
        if not state_token:
            unchanged += 1
            continue

        current_city_name = (row.city_name or "").strip()
        cleaned_city_name = _strip_suffix(current_city_name, state_token).title()

        if not cleaned_city_name:
            unchanged += 1
            continue

        # Peel the field value first if it carried the accidental suffix.
        if cleaned_city_name != current_city_name:
            frappe.db.set_value(
                "CH City",
                row.name,
                "city_name",
                cleaned_city_name,
                update_modified=False,
            )
            stripped_field += 1

        canonical = f"{cleaned_city_name}-{state_token}"
        if row.name == canonical:
            unchanged += 1
            continue

        merge = bool(frappe.db.exists("CH City", canonical))
        try:
            frappe.rename_doc(
                "CH City",
                row.name,
                canonical,
                force=True,
                merge=merge,
            )
        except Exception:
            failed += 1
            frappe.log_error(
                frappe.get_traceback(),
                f"v29 CH City rename {row.name!r} -> {canonical!r} (merge={merge})",
            )
            continue

        if merge:
            merged += 1
        else:
            renamed += 1

    frappe.db.commit()
    print(
        "v29_strip_duplicate_state_suffix_from_city: "
        f"stripped_field={stripped_field} renamed={renamed} merged={merged} "
        f"unchanged={unchanged} failed={failed}"
    )
