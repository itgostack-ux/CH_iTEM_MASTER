"""Unify CH City into a common master (non-company).

- Merge duplicate CH City rows created per company.
- Keep one canonical row per (state, city_name).
- Remap all link fields to canonical city rows.
- Clear legacy company values on CH City rows.
"""

import frappe


def execute():
    if not frappe.db.exists("DocType", "CH City"):
        return

    city_rows = frappe.get_all(
        "CH City",
        fields=["name", "city_name", "state", "creation", "modified"],
        order_by="creation asc, modified asc",
    )
    if not city_rows:
        return

    # One canonical city per (state, city_name)
    canonical_by_key = {}
    duplicate_map = {}

    for row in city_rows:
        key = ((row.state or "").strip(), (row.city_name or "").strip())
        if key not in canonical_by_key:
            canonical_by_key[key] = row.name
            continue
        duplicate_map[row.name] = canonical_by_key[key]

    if duplicate_map:
        for old_city, new_city in duplicate_map.items():
            # Remap links in all known doctypes
            _safe_update("CH Store", "city", old_city, new_city)
            _safe_update("CH Store Zone", "city", old_city, new_city)
            _safe_update("Warehouse", "ch_city", old_city, new_city)
            _safe_update("Branch", "ch_city", old_city, new_city)
            _safe_update("Address", "custom_ch_city", old_city, new_city)
            _safe_update("CH Pincode", "city", old_city, new_city)

        # Delete duplicate city docs after remap
        for old_city in duplicate_map:
            if frappe.db.exists("CH City", old_city):
                frappe.delete_doc("CH City", old_city, force=True, ignore_permissions=True)

    # Explicit cleanup of malformed legacy city IDs created during prior seeding.
    # These are not semantic city names and should point to canonical rows.
    legacy_city_map = {
        "-Bestbuy Mobiles Pvt Ltd-Mumbai": "BestBuy Mobiles Pvt Ltd-Mumbai",
        "Tamil Nadu-Bestbuy Mobiles Pvt Ltd-Chennai": "BestBuy Mobiles Pvt Ltd-Chennai",
    }
    for old_city, new_city in legacy_city_map.items():
        if not (frappe.db.exists("CH City", old_city) and frappe.db.exists("CH City", new_city)):
            continue
        _safe_update("CH Store", "city", old_city, new_city)
        _safe_update("CH Store Zone", "city", old_city, new_city)
        _safe_update("Warehouse", "ch_city", old_city, new_city)
        _safe_update("Branch", "ch_city", old_city, new_city)
        _safe_update("Address", "custom_ch_city", old_city, new_city)
        _safe_update("CH Pincode", "city", old_city, new_city)
        frappe.delete_doc("CH City", old_city, force=True, ignore_permissions=True)

    # Legacy compatibility: keep company field but blank (common master semantics)
    if _has_column("tabCH City", "company"):
        frappe.db.sql("""
            UPDATE `tabCH City`
            SET company = NULL
            WHERE IFNULL(company, '') != ''
        """)

    frappe.db.commit()
    frappe.clear_cache(doctype="CH City")


def _safe_update(doctype: str, fieldname: str, old_value: str, new_value: str):
    table = f"tab{doctype}"
    if not frappe.db.table_exists(doctype):
        return
    if not _has_column(table, fieldname):
        return

    frappe.db.sql(
        f"""
        UPDATE `{table}`
        SET `{fieldname}` = %s
        WHERE `{fieldname}` = %s
        """,
        (new_value, old_value),
    )


def _has_column(table_name: str, column: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (table_name, column),
        )
    )
