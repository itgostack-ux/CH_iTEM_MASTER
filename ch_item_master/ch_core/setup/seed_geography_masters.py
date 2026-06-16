# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt

"""
Geography Masters Setup
=======================
- Seeds all Indian states into CH State (with GST state codes).
- Seeds Tamil Nadu cities and Maharashtra cities.
- Seeds known pincodes.
- Adds custom_ch_state and custom_ch_pincode to the Address DocType.
- Backfills existing Address records to link to the right masters.

Idempotent — safe to run multiple times.

Usage:
    bench --site erpnext.local execute \
        ch_item_master.ch_core.setup.seed_geography_masters.execute
"""

import frappe

# ── Indian states (state_name, gst_state_code, iso_code) ─────────────────────
# Codes match India Compliance gst_state_number values.
INDIAN_STATES = [
    ("Andaman and Nicobar Islands", "35", "AN"),
    ("Andhra Pradesh", "37", "AP"),
    ("Arunachal Pradesh", "12", "AR"),
    ("Assam", "18", "AS"),
    ("Bihar", "10", "BR"),
    ("Chandigarh", "04", "CH"),
    ("Chhattisgarh", "22", "CG"),
    ("Dadra and Nagar Haveli and Daman and Diu", "26", "DD"),
    ("Delhi", "07", "DL"),
    ("Goa", "30", "GA"),
    ("Gujarat", "24", "GJ"),
    ("Haryana", "06", "HR"),
    ("Himachal Pradesh", "02", "HP"),
    ("Jammu and Kashmir", "01", "JK"),
    ("Jharkhand", "20", "JH"),
    ("Karnataka", "29", "KA"),
    ("Kerala", "32", "KL"),
    ("Ladakh", "38", "LA"),
    ("Lakshadweep Islands", "31", "LD"),
    ("Madhya Pradesh", "23", "MP"),
    ("Maharashtra", "27", "MH"),
    ("Manipur", "14", "MN"),
    ("Meghalaya", "17", "ML"),
    ("Mizoram", "15", "MZ"),
    ("Nagaland", "13", "NL"),
    ("Odisha", "21", "OD"),
    ("Other Countries", "97", "OC"),
    ("Other Territory", "99", "OT"),
    ("Puducherry", "34", "PY"),
    ("Punjab", "03", "PB"),
    ("Rajasthan", "08", "RJ"),
    ("Sikkim", "11", "SK"),
    ("Tamil Nadu", "33", "TN"),
    ("Telangana", "36", "TG"),
    ("Tripura", "16", "TR"),
    ("Uttar Pradesh", "09", "UP"),
    ("Uttarakhand", "05", "UK"),
    ("West Bengal", "19", "WB"),
]

# ── Cities (city_name, state_name) ─────────────────────────────────────────
# We seed for the default operating companies. Users add more via the CH City UI.
_CITIES = [
    # Tamil Nadu
    ("Chennai", "Tamil Nadu"),
    ("Coimbatore", "Tamil Nadu"),
    ("Madurai", "Tamil Nadu"),
    ("Tiruchirappalli", "Tamil Nadu"),
    ("Salem", "Tamil Nadu"),
    ("Tirunelveli", "Tamil Nadu"),
    ("Vellore", "Tamil Nadu"),
    # Maharashtra
    ("Mumbai", "Maharashtra"),
    ("Pune", "Maharashtra"),
    ("Nagpur", "Maharashtra"),
    ("Nashik", "Maharashtra"),
    ("Aurangabad", "Maharashtra"),
    # Karnataka
    ("Bengaluru", "Karnataka"),
    ("Mysuru", "Karnataka"),
    ("Hubli", "Karnataka"),
    ("Mangaluru", "Karnataka"),
    # Telangana
    ("Hyderabad", "Telangana"),
    ("Warangal", "Telangana"),
    # Delhi
    ("New Delhi", "Delhi"),
    ("Delhi", "Delhi"),
    # Gujarat
    ("Ahmedabad", "Gujarat"),
    ("Surat", "Gujarat"),
    ("Vadodara", "Gujarat"),
    ("Rajkot", "Gujarat"),
    # Rajasthan
    ("Jaipur", "Rajasthan"),
    ("Jodhpur", "Rajasthan"),
    ("Udaipur", "Rajasthan"),
    # Kerala
    ("Thiruvananthapuram", "Kerala"),
    ("Kochi", "Kerala"),
    ("Kozhikode", "Kerala"),
    # Andhra Pradesh
    ("Visakhapatnam", "Andhra Pradesh"),
    ("Vijayawada", "Andhra Pradesh"),
    ("Guntur", "Andhra Pradesh"),
    # West Bengal
    ("Kolkata", "West Bengal"),
    ("Howrah", "West Bengal"),
    ("Durgapur", "West Bengal"),
    # Uttar Pradesh
    ("Lucknow", "Uttar Pradesh"),
    ("Kanpur", "Uttar Pradesh"),
    ("Agra", "Uttar Pradesh"),
    ("Varanasi", "Uttar Pradesh"),
    ("Noida", "Uttar Pradesh"),
    ("Ghaziabad", "Uttar Pradesh"),
    # Punjab
    ("Amritsar", "Punjab"),
    ("Ludhiana", "Punjab"),
    ("Jalandhar", "Punjab"),
    # Haryana
    ("Gurugram", "Haryana"),
    ("Faridabad", "Haryana"),
    ("Ambala", "Haryana"),
    # Madhya Pradesh
    ("Indore", "Madhya Pradesh"),
    ("Bhopal", "Madhya Pradesh"),
    ("Jabalpur", "Madhya Pradesh"),
    # Bihar
    ("Patna", "Bihar"),
    ("Gaya", "Bihar"),
    # Odisha
    ("Bhubaneswar", "Odisha"),
    ("Cuttack", "Odisha"),
    # Assam
    ("Guwahati", "Assam"),
    ("Dibrugarh", "Assam"),
    # Jharkhand
    ("Ranchi", "Jharkhand"),
    ("Jamshedpur", "Jharkhand"),
    # Chhattisgarh
    ("Raipur", "Chhattisgarh"),
    ("Bilaspur", "Chhattisgarh"),
    # Uttarakhand
    ("Dehradun", "Uttarakhand"),
    ("Haridwar", "Uttarakhand"),
    # Goa
    ("Panaji", "Goa"),
    ("Vasco da Gama", "Goa"),
    # Jammu and Kashmir
    ("Srinagar", "Jammu and Kashmir"),
    ("Jammu", "Jammu and Kashmir"),
    # Himachal Pradesh
    ("Shimla", "Himachal Pradesh"),
    ("Manali", "Himachal Pradesh"),
    # Chandigarh
    ("Chandigarh", "Chandigarh"),
    # Puducherry
    ("Puducherry", "Puducherry"),
    # Tripura
    ("Agartala", "Tripura"),
    # Manipur
    ("Imphal", "Manipur"),
    # Meghalaya
    ("Shillong", "Meghalaya"),
    # Nagaland
    ("Kohima", "Nagaland"),
    # Mizoram
    ("Aizawl", "Mizoram"),
    # Arunachal Pradesh
    ("Itanagar", "Arunachal Pradesh"),
    # Sikkim
    ("Gangtok", "Sikkim"),
    # Ladakh
    ("Leh", "Ladakh"),
    # Andaman and Nicobar Islands
    ("Port Blair", "Andaman and Nicobar Islands"),
]

# ── Pincodes (pincode, city_name, state_name) ─────────────────────────────
_PINCODES = [
    # Chennai
    ("600001", "Chennai", "Tamil Nadu"),
    ("600002", "Chennai", "Tamil Nadu"),
    ("600003", "Chennai", "Tamil Nadu"),
    ("600006", "Chennai", "Tamil Nadu"),
    ("600010", "Chennai", "Tamil Nadu"),
    ("600017", "Chennai", "Tamil Nadu"),
    ("600020", "Chennai", "Tamil Nadu"),
    ("600021", "Chennai", "Tamil Nadu"),
    ("600034", "Chennai", "Tamil Nadu"),
    ("600041", "Chennai", "Tamil Nadu"),
    ("601010", "Chennai", "Tamil Nadu"),
    # Mumbai
    ("400001", "Mumbai", "Maharashtra"),
    ("400002", "Mumbai", "Maharashtra"),
    ("400003", "Mumbai", "Maharashtra"),
    ("400004", "Mumbai", "Maharashtra"),
    ("400005", "Mumbai", "Maharashtra"),
    ("400010", "Mumbai", "Maharashtra"),
    ("400018", "Mumbai", "Maharashtra"),
    ("400019", "Mumbai", "Maharashtra"),
    ("400050", "Mumbai", "Maharashtra"),
    ("400059", "Mumbai", "Maharashtra"),
    ("400063", "Mumbai", "Maharashtra"),
    ("400069", "Mumbai", "Maharashtra"),
    ("400070", "Mumbai", "Maharashtra"),
    ("400076", "Mumbai", "Maharashtra"),
    ("400080", "Mumbai", "Maharashtra"),
    ("400093", "Mumbai", "Maharashtra"),
    # Bengaluru
    ("560001", "Bengaluru", "Karnataka"),
    ("560002", "Bengaluru", "Karnataka"),
    ("560010", "Bengaluru", "Karnataka"),
    ("560025", "Bengaluru", "Karnataka"),
    ("560034", "Bengaluru", "Karnataka"),
    ("560043", "Bengaluru", "Karnataka"),
    ("560066", "Bengaluru", "Karnataka"),
    ("560100", "Bengaluru", "Karnataka"),
    # Hyderabad
    ("500001", "Hyderabad", "Telangana"),
    ("500003", "Hyderabad", "Telangana"),
    ("500016", "Hyderabad", "Telangana"),
    ("500034", "Hyderabad", "Telangana"),
    ("500081", "Hyderabad", "Telangana"),
    # Delhi
    ("110001", "New Delhi", "Delhi"),
    ("110002", "New Delhi", "Delhi"),
    ("110005", "New Delhi", "Delhi"),
    ("110051", "Delhi", "Delhi"),
    # Ahmedabad
    ("380001", "Ahmedabad", "Gujarat"),
    ("380006", "Ahmedabad", "Gujarat"),
    ("380015", "Ahmedabad", "Gujarat"),
    # Surat
    ("395001", "Surat", "Gujarat"),
    ("395002", "Surat", "Gujarat"),
    # Pune
    ("411001", "Pune", "Maharashtra"),
    ("411002", "Pune", "Maharashtra"),
    ("411007", "Pune", "Maharashtra"),
    ("411015", "Pune", "Maharashtra"),
    ("411038", "Pune", "Maharashtra"),
    # Nagpur
    ("440001", "Nagpur", "Maharashtra"),
    ("440010", "Nagpur", "Maharashtra"),
    # Kolkata
    ("700001", "Kolkata", "West Bengal"),
    ("700013", "Kolkata", "West Bengal"),
    ("700091", "Kolkata", "West Bengal"),
    # Lucknow
    ("226001", "Lucknow", "Uttar Pradesh"),
    ("226010", "Lucknow", "Uttar Pradesh"),
    # Kanpur
    ("208001", "Kanpur", "Uttar Pradesh"),
    ("208002", "Kanpur", "Uttar Pradesh"),
    # Noida
    ("201301", "Noida", "Uttar Pradesh"),
    ("201302", "Noida", "Uttar Pradesh"),
    # Agra
    ("282001", "Agra", "Uttar Pradesh"),
    # Patna
    ("800001", "Patna", "Bihar"),
    ("800013", "Patna", "Bihar"),
    # Bhubaneswar
    ("751001", "Bhubaneswar", "Odisha"),
    ("751010", "Bhubaneswar", "Odisha"),
    # Ludhiana
    ("141001", "Ludhiana", "Punjab"),
    ("141003", "Ludhiana", "Punjab"),
    # Amritsar
    ("143001", "Amritsar", "Punjab"),
    # Gurugram
    ("122001", "Gurugram", "Haryana"),
    ("122002", "Gurugram", "Haryana"),
    # Indore
    ("452001", "Indore", "Madhya Pradesh"),
    ("452010", "Indore", "Madhya Pradesh"),
    # Bhopal
    ("462001", "Bhopal", "Madhya Pradesh"),
    ("462011", "Bhopal", "Madhya Pradesh"),
    # Guwahati
    ("781001", "Guwahati", "Assam"),
    ("781003", "Guwahati", "Assam"),
    # Ranchi
    ("834001", "Ranchi", "Jharkhand"),
    ("834002", "Ranchi", "Jharkhand"),
    # Raipur
    ("492001", "Raipur", "Chhattisgarh"),
    # Jaipur
    ("302001", "Jaipur", "Rajasthan"),
    ("302004", "Jaipur", "Rajasthan"),
    ("302017", "Jaipur", "Rajasthan"),
    # Dehradun
    ("248001", "Dehradun", "Uttarakhand"),
    ("248002", "Dehradun", "Uttarakhand"),
    # Panaji
    ("403001", "Panaji", "Goa"),
    # Chandigarh
    ("160001", "Chandigarh", "Chandigarh"),
    ("160017", "Chandigarh", "Chandigarh"),
    # Puducherry
    ("605001", "Puducherry", "Puducherry"),
    ("605002", "Puducherry", "Puducherry"),
    # Shimla
    ("171001", "Shimla", "Himachal Pradesh"),
    # Srinagar
    ("190001", "Srinagar", "Jammu and Kashmir"),
    # Jammu
    ("180001", "Jammu", "Jammu and Kashmir"),
    # Agartala
    ("799001", "Agartala", "Tripura"),
    # Imphal
    ("795001", "Imphal", "Manipur"),
    # Shillong
    ("793001", "Shillong", "Meghalaya"),
    # Kohima
    ("797001", "Kohima", "Nagaland"),
    # Aizawl
    ("796001", "Aizawl", "Mizoram"),
    # Itanagar
    ("791111", "Itanagar", "Arunachal Pradesh"),
    # Gangtok
    ("737101", "Gangtok", "Sikkim"),
    # Leh
    ("194101", "Leh", "Ladakh"),
    # Port Blair
    ("744101", "Port Blair", "Andaman and Nicobar Islands"),
    # Visakhapatnam
    ("530001", "Visakhapatnam", "Andhra Pradesh"),
    ("530003", "Visakhapatnam", "Andhra Pradesh"),
    # Vijayawada
    ("520001", "Vijayawada", "Andhra Pradesh"),
    ("520010", "Vijayawada", "Andhra Pradesh"),
    # Mysuru
    ("570001", "Mysuru", "Karnataka"),
    ("570004", "Mysuru", "Karnataka"),
    # Thiruvananthapuram
    ("695001", "Thiruvananthapuram", "Kerala"),
    ("695004", "Thiruvananthapuram", "Kerala"),
    # Kochi
    ("682001", "Kochi", "Kerala"),
    ("682011", "Kochi", "Kerala"),
    # Coimbatore
    ("641001", "Coimbatore", "Tamil Nadu"),
    ("641002", "Coimbatore", "Tamil Nadu"),
    ("641018", "Coimbatore", "Tamil Nadu"),
    # Madurai
    ("625001", "Madurai", "Tamil Nadu"),
    ("625002", "Madurai", "Tamil Nadu"),
    # Hyderabad
    ("500001", "Hyderabad", "Telangana"),
    ("500003", "Hyderabad", "Telangana"),
    ("500016", "Hyderabad", "Telangana"),
    ("500034", "Hyderabad", "Telangana"),
    ("500081", "Hyderabad", "Telangana"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ok(msg):
    print(f"  ✅ {msg}")


def _skip(msg):
    print(f"  ⏭  {msg}")


def _warn(msg):
    print(f"  ⚠️  {msg}")


# ── 1. Seed CH State ──────────────────────────────────────────────────────────

def seed_states():
    created = skipped = 0
    for state_name, state_code, _ in INDIAN_STATES:
        if frappe.db.exists("CH State", state_name):
            # Update state_code if it's missing
            existing_code = frappe.db.get_value("CH State", state_name, "state_code")
            if not existing_code:
                frappe.db.set_value("CH State", state_name, "state_code", state_code)
                _ok(f"Updated state_code for {state_name} → {state_code}")
            skipped += 1
            continue
        doc = frappe.get_doc({
            "doctype": "CH State",
            "state_name": state_name,
            "state_code": state_code,
            "country": "India",
        })
        doc.insert(ignore_permissions=True)
        created += 1
    frappe.db.commit()
    print(f"  CH State  — created: {created}, skipped: {skipped}")


# ── 2. Seed CH City ───────────────────────────────────────────────────────────

def _get_operating_companies():
    rows = frappe.db.sql(
        "SELECT name FROM `tabCompany` WHERE name IN ('BestBuy Mobiles Pvt Ltd','GOFIX SOLUTIONS PRIVATE LIMITED')",
        as_dict=True,
    )
    if not rows:
        rows = frappe.db.sql(
            "SELECT name FROM `tabCompany` WHERE IFNULL(is_group,0)=0 LIMIT 2",
            as_dict=True,
        )
    return [r.name for r in rows]


def seed_cities():
    companies = _get_operating_companies()
    if not companies:
        _warn("No operating company found — cities not seeded")
        return

    created = skipped = 0
    for company in companies:
        for city_name, state_name in _CITIES:
            city_doc_name = f"{company}-{city_name}"
            if frappe.db.exists("CH City", city_doc_name):
                # Fix NULL state if missing
                existing_state = frappe.db.get_value("CH City", city_doc_name, "state")
                if not existing_state and frappe.db.exists("CH State", state_name):
                    frappe.db.set_value("CH City", city_doc_name, "state", state_name)
                skipped += 1
                continue
            if not frappe.db.exists("CH State", state_name):
                _warn(f"State '{state_name}' not in CH State — skipping city {city_name} for {company}")
                continue
            doc = frappe.get_doc({
                "doctype": "CH City",
                "city_name": city_name,
                "company": company,
                "country": "India",
                "state": state_name,
            })
            doc.insert(ignore_permissions=True)
            created += 1
    frappe.db.commit()
    print(f"  CH City   — created: {created}, skipped: {skipped} (companies: {companies})")


# ── 3. Seed CH Pincode ────────────────────────────────────────────────────────

def seed_pincodes():
    if not frappe.db.table_exists("CH Pincode"):
        _warn("CH Pincode table does not exist — run bench migrate first")
        return

    companies = _get_operating_companies()
    primary_company = companies[0] if companies else None
    created = skipped = 0

    for pincode, city_name, state_name in _PINCODES:
        if frappe.db.exists("CH Pincode", pincode):
            skipped += 1
            continue

        # Find CH City record for this city
        city_doc_name = None
        if primary_company:
            city_doc_name = f"{primary_company}-{city_name}"
            if not frappe.db.exists("CH City", city_doc_name):
                city_doc_name = None

        if not city_doc_name:
            # Search by city_name across companies
            city_doc_name = frappe.db.get_value("CH City", {"city_name": city_name}, "name")

        if not city_doc_name:
            _warn(f"CH City '{city_name}' not found — skipping pincode {pincode}")
            continue

        if not frappe.db.exists("CH State", state_name):
            _warn(f"CH State '{state_name}' not found — skipping pincode {pincode}")
            continue

        doc = frappe.get_doc({
            "doctype": "CH Pincode",
            "pincode": pincode,
            "city": city_doc_name,
            "state": state_name,
            "country": "India",
        })
        doc.insert(ignore_permissions=True)
        created += 1

    frappe.db.commit()
    print(f"  CH Pincode — created: {created}, skipped: {skipped}")


# ── 4. Add custom fields to Address ──────────────────────────────────────────

_ADDRESS_CUSTOM_FIELDS = [
    {
        "fieldname": "custom_ch_state",
        "label": "State (CH)",
        "fieldtype": "Link",
        "options": "CH State",
        "insert_after": "state",
        "description": "Linked state master — auto-fills the State field above",
        "in_standard_filter": 1,
        "search_index": 1,
    },
    {
        # custom_ch_city already exists — skip if present, update if needed
        "fieldname": "custom_ch_city",
        "label": "City (CH)",
        "fieldtype": "Link",
        "options": "CH City",
        "insert_after": "city",
        "description": "Linked city master — auto-fills the City field above",
        "in_standard_filter": 1,
        "search_index": 1,
    },
    {
        "fieldname": "custom_ch_pincode",
        "label": "Pincode (CH)",
        "fieldtype": "Link",
        "options": "CH Pincode",
        "insert_after": "pincode",
        "description": "Linked pincode master — auto-fills the Postal Code field above",
        "in_standard_filter": 1,
        "search_index": 1,
    },
]


def add_address_custom_fields():
    created = skipped = 0
    for field in _ADDRESS_CUSTOM_FIELDS:
        existing = frappe.db.exists("Custom Field", {"dt": "Address", "fieldname": field["fieldname"]})
        if existing:
            skipped += 1
            continue
        cf = frappe.get_doc({"doctype": "Custom Field", "dt": "Address", **field})
        cf.insert(ignore_permissions=True)
        created += 1
        _ok(f"Custom Field: Address.{field['fieldname']}")
    if created:
        frappe.db.commit()
    print(f"  Address Custom Fields — created: {created}, skipped: {skipped}")


# ── 5. Backfill existing Address records ─────────────────────────────────────

def backfill_addresses():
    """Link existing Address records to the correct CH State / CH City / CH Pincode."""
    addresses = frappe.db.sql(
        """SELECT name, city, state, pincode
           FROM `tabAddress`
           WHERE country = 'India'
             AND (city IS NOT NULL OR state IS NOT NULL OR pincode IS NOT NULL)""",
        as_dict=True,
    )
    updated = skipped = 0
    for addr in addresses:
        updates = {}

        # Match CH State
        if addr.state:
            state_name = frappe.db.get_value("CH State", addr.state.strip(), "name")
            if state_name:
                current = frappe.db.get_value("Address", addr.name, "custom_ch_state")
                if not current:
                    updates["custom_ch_state"] = state_name

        # Match CH City (try city_name match, state-aware)
        if addr.city:
            city_doc = frappe.db.get_value(
                "CH City",
                {"city_name": addr.city.strip()},
                "name",
                order_by="modified DESC",
            )
            if city_doc:
                current = frappe.db.get_value("Address", addr.name, "custom_ch_city")
                if not current:
                    updates["custom_ch_city"] = city_doc

        # Match CH Pincode
        if addr.pincode and frappe.db.table_exists("CH Pincode"):
            pin_doc = frappe.db.get_value("CH Pincode", addr.pincode.strip(), "name")
            if pin_doc:
                current = frappe.db.get_value("Address", addr.name, "custom_ch_pincode")
                if not current:
                    updates["custom_ch_pincode"] = pin_doc

        if updates:
            frappe.db.set_value("Address", addr.name, updates)
            updated += 1
        else:
            skipped += 1

    frappe.db.commit()
    print(f"  Addresses backfilled: {updated} updated, {skipped} skipped/unmatched")


# ── Entry point ───────────────────────────────────────────────────────────────

def execute():
    print("\n" + "=" * 60)
    print("  Geography Masters — Seeding")
    print("=" * 60)

    print("\n── States ──")
    seed_states()

    print("\n── Cities ──")
    seed_cities()

    print("\n── Pincodes ──")
    seed_pincodes()

    print("\n── Address Custom Fields ──")
    add_address_custom_fields()

    print("\n── Address Backfill ──")
    backfill_addresses()

    print("\n" + "=" * 60)
    print("  Done")
    print("=" * 60 + "\n")
