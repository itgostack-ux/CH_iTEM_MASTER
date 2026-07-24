"""Location Hierarchy — golden dataset export / import (Phase E).

Enables three concrete workflows:

  1. Fresh-site bootstrap — dump the reference retail geography from a
     production site and re-seed a new staging / DR / tenant site with
     the same masters (States → Cities → Zones → Stores → Sellable
     warehouses).  Bins / store-group / zone-group / city-group
     warehouses are AUTO-CREATED by ``ch_store.after_insert`` and
     ``warehouse_geo.ensure_store_group`` respectively, so the seed
     only captures the seed-level entities and lets the existing
     hooks materialise the derived tree.

  2. Refresh loop — re-import the same JSON periodically to backfill
     any masters that were added out-of-band on the target site
     (idempotent by natural key: state_name, city_name,
     zone_name+company+city, store_name+company).

  3. Change control — commit the exported JSON to git and diff
     across releases to detect unauthorised store openings /
     closures / renames.  Matches SAP Landscape Transport (SLT) and
     Oracle Fusion Configuration Package patterns for master-data
     movement between environments.

Design notes
------------

* Companies are exported as a MINIMAL identity record (company_name,
  abbr, country, default_currency) and auto-created on import when no
  Company with the same abbr exists.  Chart-of-accounts / taxes are
  still per-site: creating the Company triggers ERPNext's standard
  CoA provisioning for the country; GST registrations etc. remain a
  site-local follow-up.  Zones / stores still resolve their company
  by *abbreviation* (optionally overridden via ``company_map``), so
  the same JSON keeps working against sites whose companies are
  named differently.

* City references are resolved by NATURAL KEY (city_name + state),
  not by primary key.  ``CHCity.autoname`` derives the PK
  deterministically as ``{City}-{state_code}`` (see patch
  v28_canonicalise_city_pks which converges legacy sites to that
  form), but the importer never trusts raw PKs from the source site:
  each zone/store entry carries ``city_name`` + ``city_state`` and is
  resolved through ``location_hierarchy.ensure_city``.

* Warehouses are exported by their **base name** (the part before
  the ``" - <abbr>"`` suffix Frappe adds) plus the source-company
  abbr.  On import we resolve the target-site name with the target
  company's abbr.  This lets the same JSON seed sites where the
  companies are named / abbreviated differently.

* Only the ``source_warehouse`` on Zone and ``warehouse`` (Sellable
  leaf) on Store are exported as warehouse references.  Everything
  else (Store Group, Bin leaves, Zone Group, City Group) is a
  derived object created by our existing hooks.

* Idempotent by natural key — re-running import over an already-
  seeded site is a no-op that reports what would have been skipped.

* Dry-run first — every import call prints a PLAN before mutating,
  and the CLI defaults to dry-run.

Usage
-----

Export (via bench execute or click CLI)::

    bench --site erpnext.local execute \\
        ch_item_master.ch_core.location_hierarchy_seed.export_to_file \\
        --kwargs "{'out_path': '/tmp/geo.json'}"

    bench --site erpnext.local ch-locations-export --out /tmp/geo.json

Import (dry-run is the default)::

    bench --site erpnext.local ch-locations-import --in /tmp/geo.json
    bench --site erpnext.local ch-locations-import --in /tmp/geo.json --apply
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

import frappe

from ch_item_master.config import iter_all_rows


# Format version — bump when the schema of the JSON changes.  Import
# refuses to load a file whose version is newer than what this module
# knows about.
#   v1 — states/cities/zones/stores, raw city PK references.
#   v2 — adds ``companies`` section (auto-created when missing) and
#        ``city_name``/``city_state`` natural keys on zones + stores.
SEED_SCHEMA_VERSION = 2
BASELINE_RELATIVE_PATH = os.path.join("data", "seed", "location_hierarchy_ch_baseline.json")

# Fields captured per doctype.  We deliberately DON'T dump audit
# columns (creation, modified, owner, _user_tags, _assign, _liked_by,
# _comments, docstatus, idx, name) — those are site-local metadata.
_COMPANY_FIELDS = ("company_name", "abbr", "country", "default_currency")
_STATE_FIELDS = ("state_name", "state_code", "country", "disabled", "description")
_CITY_FIELDS = ("city_name", "state", "country", "disabled", "description")
_ZONE_FIELDS = ("zone_name", "city", "description")
_STORE_FIELDS = (
    "store_name",
    "store_code",
    "city",
    "zone",
    "address",
    "pincode",
    "contact_phone",
    "territory",
    "branch",
    "is_buyback_enabled",
    "is_service_enabled",
    "is_retail_enabled",
    "latitude",
    "longitude",
    "google_maps_url",
    "custom_latitude",
    "custom_longitude",
    "store_status",
    "opening_date",
    "disabled",
)


# Legacy → current abbr aliases. The baseline seed data was exported
# when Company.abbr was 4 chars (BMPL / GSPL). The `gofix` rename patch
# (``rename_company_abbrs_to_2char``) flipped them to the enforced
# 2-char business rule (BM / GF). This alias table lets the seed
# resolve baseline entries on both pre- and post-migration sites.
# Add new entries only if a Company.abbr is renamed again in the future.
_LEGACY_ABBR_ALIASES = {
    "BMPL": "BM",
    "GSPL": "GF",
}


def _company_abbr(company: str) -> str:
    if not company:
        return ""
    return frappe.db.get_value("Company", company, "abbr") or ""


def _company_from_abbr(abbr: str) -> str | None:
    if not abbr:
        return None
    name = frappe.db.get_value("Company", {"abbr": abbr}, "name")
    if name:
        return name
    # Fall back to the current abbr if this is a known legacy alias.
    aliased = _LEGACY_ABBR_ALIASES.get(abbr)
    if aliased:
        return frappe.db.get_value("Company", {"abbr": aliased}, "name")
    return None


def _warehouse_base_name(full_name: str, abbr: str) -> str:
    """Strip the trailing ``" - <abbr>"`` Frappe attaches to warehouses.

    Falls back to the raw name if no abbr / no match.
    """
    if not (full_name and abbr):
        return full_name or ""
    tail = f" - {abbr}"
    if full_name.endswith(tail):
        return full_name[: -len(tail)]
    return full_name


def _warehouse_target_name(base_name: str, target_company: str) -> str:
    """Reconstruct the target-site Warehouse name for a base + company."""
    if not base_name:
        return ""
    abbr = _company_abbr(target_company)
    if not abbr:
        return base_name
    candidate = f"{base_name} - {abbr}"
    if frappe.db.exists("Warehouse", candidate):
        return candidate
    # Some legacy warehouses were created without the abbr suffix.
    if frappe.db.exists("Warehouse", base_name):
        return base_name
    return candidate  # doesn't exist yet — caller decides what to do


# ---------------------------------------------------------------------------
# EXPORT
# ---------------------------------------------------------------------------


def _city_natural_key(city_pk: str | None) -> dict:
    """Return ``{city_name, city_state}`` for a CH City PK (empty when unknown)."""
    if not city_pk:
        return {"city_name": None, "city_state": None}
    row = frappe.db.get_value("CH City", city_pk, ["city_name", "state"], as_dict=True)
    if not row:
        return {"city_name": None, "city_state": None}
    return {"city_name": row.city_name, "city_state": row.state}


def _export_companies(company_filter: Iterable[str] | None) -> list[dict]:
    """Export the minimal identity of every company referenced by zones/stores."""
    names: set[str] = set()
    for doctype in ("CH Store Zone", "CH Store"):
        filters: dict[str, Any] = {}
        if company_filter:
            filters["company"] = ["in", list(company_filter)]
        names.update(
            r.company
            for r in iter_all_rows(
                doctype,
                filters=filters,
                fields=["company"],
                order_by="name asc",
            )
            if r.company
        )
    out = []
    for name in sorted(names):
        row = frappe.db.get_value(
            "Company", name, ["name", "abbr", "country", "default_currency"], as_dict=True
        )
        if row:
            out.append(
                {
                    "company_name": row.name,
                    "abbr": row.abbr,
                    "country": row.country,
                    "default_currency": row.default_currency,
                }
            )
    return out


def _export_states() -> list[dict]:
    rows = iter_all_rows("CH State", fields=list(_STATE_FIELDS), order_by="state_name, name")
    return [dict(r) for r in rows]


def _export_cities() -> list[dict]:
    rows = iter_all_rows(
        "CH City", fields=list(_CITY_FIELDS), order_by="city_name, name"
    )
    return [dict(r) for r in rows]


def _export_zones(company_filter: Iterable[str] | None) -> list[dict]:
    filters: dict[str, Any] = {}
    if company_filter:
        filters["company"] = ["in", list(company_filter)]
    rows = iter_all_rows(
        "CH Store Zone",
        filters=filters,
        fields=[*_ZONE_FIELDS, "company", "source_warehouse"],
        order_by="company, zone_name, name",
    )
    out = []
    for r in rows:
        abbr = _company_abbr(r.company)
        out.append(
            {
                **{k: r.get(k) for k in _ZONE_FIELDS},
                **_city_natural_key(r.city),
                "company_abbr": abbr,
                "source_warehouse_base": _warehouse_base_name(
                    r.source_warehouse, abbr
                ),
            }
        )
    return out


def _export_stores(company_filter: Iterable[str] | None) -> list[dict]:
    filters: dict[str, Any] = {}
    if company_filter:
        filters["company"] = ["in", list(company_filter)]
    rows = iter_all_rows(
        "CH Store",
        filters=filters,
        fields=[*_STORE_FIELDS, "company", "warehouse"],
        order_by="company, store_code, name",
    )
    out = []
    for r in rows:
        abbr = _company_abbr(r.company)
        out.append(
            {
                **{k: r.get(k) for k in _STORE_FIELDS},
                **_city_natural_key(r.city),
                "company_abbr": abbr,
                "warehouse_base": _warehouse_base_name(r.warehouse, abbr),
            }
        )
    return out


def export_location_hierarchy(company: str | None = None) -> dict:
    """Return a JSON-serialisable snapshot of the retail geography.

    Parameters
    ----------
    company : str, optional
        When provided, restrict Zones and Stores to that company (States
        and Cities are always exported in full — they are cross-company
        masters).  Omit to dump every company on the source site.
    """
    company_filter = [company] if company else None
    payload = {
        "schema_version": SEED_SCHEMA_VERSION,
        "source_site": frappe.local.site,
        "exported_at": frappe.utils.now(),
        "filter_company": company,
        "companies": _export_companies(company_filter),
        "states": _export_states(),
        "cities": _export_cities(),
        "zones": _export_zones(company_filter),
        "stores": _export_stores(company_filter),
    }
    payload["counts"] = {
        k: len(payload[k]) for k in ("companies", "states", "cities", "zones", "stores")
    }
    return payload


def export_to_file(out_path: str, company: str | None = None) -> dict:
    """Write the export to disk and return the summary dict."""
    if not out_path:
        frappe.throw("out_path is required")
    payload = export_location_hierarchy(company=company)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    return {
        "written_to": out_path,
        "counts": payload["counts"],
        "schema_version": payload["schema_version"],
    }


# ---------------------------------------------------------------------------
# IMPORT
# ---------------------------------------------------------------------------


def _resolve_company(entry: dict, company_map: dict | None) -> str | None:
    """Resolve the target-site Company name for an exported entry.

    Precedence:
      1. Explicit ``company_map[source_abbr]`` mapping
      2. Same abbr on target site
      3. None → caller decides (skip / throw)
    """
    src_abbr = entry.get("company_abbr")
    if not src_abbr:
        return None
    if company_map and src_abbr in company_map:
        return company_map[src_abbr]
    return _company_from_abbr(src_abbr)


def _ensure_dependent_masters(plan: dict) -> None:
    """Ensure Link targets required by Company + CH Store inserts exist.

    On a brand-new site the seeder can run before ERPNext's Setup Wizard
    has provisioned the Territory root and the "Transit" Warehouse Type,
    which then breaks:

      * ``Company.create_default_warehouses`` (fires on first Company
        insert) needs Warehouse Type ``Transit`` for the auto-created
        "Goods In Transit - <abbr>" warehouse.
      * ``CH Store._upsert_store`` writes ``territory = "All Territories"``
        from the seed baseline, which needs the root Territory to exist.

    Both are cheap, single-field inserts. Idempotent: if the master
    already exists we no-op. Every miss is logged as a manual follow-up
    (never an error) so seed output stays actionable.
    """
    # 1. Warehouse Type: Transit
    try:
        if not frappe.db.exists("Warehouse Type", "Transit"):
            doc = frappe.new_doc("Warehouse Type")
            doc.name = "Transit"
            doc.flags.ignore_permissions = True
            doc.flags.ignore_mandatory = True
            doc.insert()
            plan["to_create"].append(
                {"type": "Warehouse Type", "name": "Transit", "reason": "fresh-site prerequisite"}
            )
    except Exception as e:  # noqa: BLE001
        plan["manual_followups"].append(
            {
                "type": "Warehouse Type",
                "name": "Transit",
                "reason": f"could not auto-create ({e}); create manually before re-running seed",
            }
        )

    # 2. Territory: All Territories (root group)
    try:
        if not frappe.db.exists("Territory", "All Territories"):
            doc = frappe.new_doc("Territory")
            doc.territory_name = "All Territories"
            doc.is_group = 1
            doc.parent_territory = None
            doc.flags.ignore_permissions = True
            doc.flags.ignore_mandatory = True
            doc.insert()
            plan["to_create"].append(
                {"type": "Territory", "name": "All Territories", "reason": "fresh-site prerequisite"}
            )
    except Exception as e:  # noqa: BLE001
        plan["manual_followups"].append(
            {
                "type": "Territory",
                "name": "All Territories",
                "reason": f"could not auto-create ({e}); create manually before re-running seed",
            }
        )


def _upsert_company(entry: dict, plan: dict, company_map: dict | None) -> None:
    """Create a minimal Company when no company with the entry's abbr exists.

    Only identity fields are seeded (name, abbr, country, currency) —
    inserting the Company lets ERPNext provision its standard CoA / cost
    centers for the country. If a Company already exists under the same
    NAME but a different abbr, we do NOT touch it (changing an abbr
    rewrites every account/warehouse suffix) — that conflict is recorded
    as a manual follow-up instead.
    """
    abbr = (entry.get("abbr") or "").strip()
    name = (entry.get("company_name") or "").strip()
    if not (abbr and name):
        plan["skipped"].append({"type": "Company", "reason": "missing company_name/abbr", "entry": entry})
        return

    # A company_map override means the operator already mapped this abbr
    # to an existing target company — never auto-create in that case.
    if company_map and abbr in company_map:
        plan["skipped"].append(
            {"type": "Company", "name": company_map[abbr], "reason": f"mapped via company_map[{abbr}]"}
        )
        return

    existing = _company_from_abbr(abbr)
    if existing:
        plan["skipped"].append({"type": "Company", "name": existing, "reason": "already exists"})
        return

    if frappe.db.exists("Company", name):
        plan["manual_followups"].append(
            {
                "type": "Company",
                "name": name,
                "reason": (
                    f"company exists but its abbr != {abbr!r} — fix the abbr manually "
                    "or pass a company_map override"
                ),
            }
        )
        return

    # Never create a company under a legacy 4-char abbr: seed files
    # exported before the 2-char rename (BMPL/GSPL) would otherwise
    # bootstrap a fresh site straight into the old naming, splitting the
    # warehouse tree ("X - BMPL" vs "X - BM") the moment any post-rename
    # backfill runs. Auto-created companies always use the current abbr.
    create_abbr = _LEGACY_ABBR_ALIASES.get(abbr, abbr)

    values = {k: entry.get(k) for k in _COMPANY_FIELDS}
    values["abbr"] = create_abbr
    plan["to_create"].append({"type": "Company", "name": name, "values": values})
    if not plan["dry_run"]:
        doc = frappe.new_doc("Company")
        doc.company_name = name
        doc.abbr = create_abbr
        doc.country = entry.get("country") or "India"
        doc.default_currency = entry.get("default_currency") or "INR"
        doc.insert(ignore_permissions=True)


def _resolve_city_ref(entry: dict, plan: dict, parent_type: str, parent_label: str) -> str | None:
    """Resolve an entry's city reference to a target-site CH City PK.

    Never trusts the source-site PK alone: PKs differ across sites that
    predate the state-aware autoname (``Chennai`` vs ``Chennai-33``).
    Resolution ladder:
      1. raw ``city`` value as PK (fast path — canonical sites match).
      2. ``ensure_city`` with the exported natural key (city_name, state)
         — resolves by natural key and creates the city if the states/
         cities sections somehow missed it (deterministic autoname PK).
      3. legacy files without natural keys: treat the raw PK as a
         city_name (true for every pre-v28 site, where PK == city_name).
    """
    raw = entry.get("city")
    if raw and frappe.db.exists("CH City", raw):
        return raw

    from ch_item_master.ch_core.location_hierarchy import ensure_city

    resolved = ensure_city(None, entry.get("city_name") or raw, entry.get("city_state"))
    if not resolved:
        plan["manual_followups"].append(
            {
                "type": parent_type,
                "name": parent_label,
                "reason": f"could not resolve CH City for city={raw!r} city_name={entry.get('city_name')!r}",
            }
        )
    return resolved


def _upsert_state(entry: dict, plan: dict) -> None:
    name = entry.get("state_name")
    if not name:
        plan["skipped"].append({"type": "CH State", "reason": "no state_name", "entry": entry})
        return
    if frappe.db.exists("CH State", name):
        plan["skipped"].append({"type": "CH State", "name": name, "reason": "already exists"})
        return
    plan["to_create"].append({"type": "CH State", "name": name, "values": {k: entry.get(k) for k in _STATE_FIELDS}})
    if not plan["dry_run"]:
        doc = frappe.new_doc("CH State")
        for k in _STATE_FIELDS:
            if entry.get(k) is not None:
                setattr(doc, k, entry.get(k))
        doc.insert(ignore_permissions=True)


def _canonical_city_pk(city_name: str, state: str | None) -> str | None:
    """Compute the deterministic PK ``CHCity.autoname`` would assign."""
    from ch_item_master.ch_core.doctype.ch_city.ch_city import (
        _resolve_state_token,
        _strip_state_suffix,
    )

    city = (city_name or "").strip().title()
    if not city:
        return None
    token = _resolve_state_token(state)
    city = _strip_state_suffix(city, token)
    return f"{city}-{token}" if token else city


def _heal_existing_city(existing: str, entry: dict, plan: dict) -> str:
    """Backfill missing state/country on a matched row and converge its PK.

    Legacy sites carry city rows created before states were mandatory
    (state = NULL). Those rows break the Location Hierarchy "Region"
    roll-up (region == state, so stateless cities render under
    "Unassigned Region") and keep a non-canonical PK. Healing:

      1. backfill ``state`` / ``country`` from the seed when empty,
      2. merge any stateless same-name twin into this row,
      3. rename to the canonical ``{City}-{state_code}`` PK
         (``frappe.rename_doc`` re-points every Link automatically).

    Returns the row's final PK.
    """
    row = frappe.db.get_value(
        "CH City", existing, ["name", "city_name", "state", "country"], as_dict=True
    )
    if not row:
        return existing
    actions = []

    state = entry.get("state")
    if state and not row.state:
        actions.append(f"state -> {state}")
        row.state = state
        if not plan["dry_run"]:
            frappe.db.set_value("CH City", existing, "state", state, update_modified=False)
    if entry.get("country") and not row.country:
        actions.append(f"country -> {entry['country']}")
        if not plan["dry_run"]:
            frappe.db.set_value(
                "CH City", existing, "country", entry["country"], update_modified=False
            )

    # Merge a stateless twin with the same city_name (e.g. a bare
    # ``Chennai`` created by an old import while ``Chennai-33`` exists).
    # NEVER merge across different states — same-named districts in two
    # states are distinct real entities.
    twin = frappe.db.get_value(
        "CH City",
        {"city_name": row.city_name, "state": ("is", "not set"), "name": ("!=", existing)},
        "name",
    )
    if twin:
        actions.append(f"merge twin {twin}")
        if not plan["dry_run"]:
            try:
                frappe.rename_doc("CH City", twin, existing, force=True, merge=True)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"seed CH City merge twin {twin} -> {existing}")

    final = existing
    canonical = _canonical_city_pk(row.city_name, row.state)
    if canonical and existing != canonical:
        actions.append(f"rename -> {canonical}")
        if not plan["dry_run"]:
            try:
                merge = bool(frappe.db.exists("CH City", canonical))
                frappe.rename_doc("CH City", existing, canonical, force=True, merge=merge)
                final = canonical
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(), f"seed CH City canonicalise {existing} -> {canonical}"
                )

    if actions:
        plan["updated"].append({"type": "CH City", "name": existing, "actions": actions})
    return final


def _upsert_city(entry: dict, plan: dict) -> None:
    city_name = entry.get("city_name")
    state = entry.get("state")
    if not city_name:
        plan["skipped"].append({"type": "CH City", "reason": "no city_name", "entry": entry})
        return
    # CH City autoname is ``{city_name}-{state_code}`` (see
    # ``ch_city.py::autoname``) — the ``name`` (PK) is NOT equal to
    # ``city_name`` when state carries a state_code.  Match by the
    # composite natural key (city_name, state) so hyphenated /
    # renamed districts don't get duplicated on re-import.
    filters: dict[str, Any] = {"city_name": city_name}
    if state:
        filters["state"] = state
    existing = frappe.db.get_value("CH City", filters, "name")
    if not existing and state:
        # Legacy rows imported before states were mandatory carry
        # state = NULL and would otherwise duplicate on import.
        existing = frappe.db.get_value(
            "CH City", {"city_name": city_name, "state": ("is", "not set")}, "name"
        )
    if existing:
        _heal_existing_city(existing, entry, plan)
        plan["skipped"].append({"type": "CH City", "name": existing, "reason": "already exists"})
        return
    plan["to_create"].append({"type": "CH City", "city_name": city_name, "values": {k: entry.get(k) for k in _CITY_FIELDS}})
    if not plan["dry_run"]:
        doc = frappe.new_doc("CH City")
        for k in _CITY_FIELDS:
            if entry.get(k) is not None:
                setattr(doc, k, entry.get(k))
        doc.insert(ignore_permissions=True)


def _ensure_seed_zone_warehouse(base_name: str, company: str, city: str, plan: dict) -> str | None:
    """Ensure the seed's zone source warehouse exists.

    Zone hubs are derived infrastructure for the hierarchy. A fresh site will
    not have them yet, so the baseline importer must materialise them instead
    of asking operators to create them first.
    """
    if not (base_name and company and city):
        return None

    target = _warehouse_target_name(base_name, company)
    if frappe.db.exists("Warehouse", target):
        return target

    plan["to_create"].append(
        {
            "type": "Warehouse",
            "name": target,
            "values": {
                "warehouse_name": base_name,
                "company": company,
                "city": city,
                "ch_location_type": "Zone Warehouse",
            },
        }
    )
    if plan["dry_run"]:
        return target

    from ch_item_master.ch_core.warehouse_geo import ensure_city_group

    parent = ensure_city_group(company, city)
    wh = frappe.new_doc("Warehouse")
    wh.warehouse_name = base_name
    wh.company = company
    wh.is_group = 0
    if parent:
        wh.parent_warehouse = parent
    wh.ch_city = city
    wh.ch_location_type = "Zone Warehouse"
    wh.insert(ignore_permissions=True)
    return wh.name


def _upsert_zone(entry: dict, plan: dict, company_map: dict | None) -> None:
    company = _resolve_company(entry, company_map)
    if not company:
        plan["skipped"].append(
            {
                "type": "CH Store Zone",
                "zone_name": entry.get("zone_name"),
                "reason": f"no target Company for abbr={entry.get('company_abbr')}",
            }
        )
        return

    zone_name = entry.get("zone_name")
    city = _resolve_city_ref(entry, plan, "CH Store Zone", entry.get("zone_name") or "?")
    if not (zone_name and city):
        plan["skipped"].append({"type": "CH Store Zone", "reason": "missing zone_name/city", "entry": entry})
        return

    existing = frappe.db.get_value(
        "CH Store Zone",
        {"zone_name": zone_name, "company": company, "city": city},
        "name",
    )
    if existing:
        if not plan["dry_run"]:
            current_source = frappe.db.get_value("CH Store Zone", existing, "source_warehouse")
            if not current_source:
                source_wh = _ensure_seed_zone_warehouse(
                    entry.get("source_warehouse_base"), company, city, plan
                )
                if source_wh:
                    frappe.db.set_value(
                        "CH Store Zone", existing, "source_warehouse", source_wh,
                        update_modified=False,
                    )
                    from ch_item_master.ch_core.location_hierarchy import sync_zone_source_warehouse_metadata

                    sync_zone_source_warehouse_metadata(existing)
        plan["skipped"].append({"type": "CH Store Zone", "name": existing, "reason": "already exists"})
        return

    source_wh = _ensure_seed_zone_warehouse(
        entry.get("source_warehouse_base"), company, city, plan
    )

    values = {k: entry.get(k) for k in _ZONE_FIELDS}
    values["city"] = city
    values["company"] = company
    values["source_warehouse"] = source_wh
    plan["to_create"].append({"type": "CH Store Zone", "zone_name": zone_name, "values": values})
    if not plan["dry_run"]:
        doc = frappe.new_doc("CH Store Zone")
        for k, v in values.items():
            if v is not None:
                setattr(doc, k, v)
        doc.insert(ignore_permissions=True)


def _upsert_store(entry: dict, plan: dict, company_map: dict | None) -> None:
    company = _resolve_company(entry, company_map)
    if not company:
        plan["skipped"].append(
            {
                "type": "CH Store",
                "store_name": entry.get("store_name"),
                "reason": f"no target Company for abbr={entry.get('company_abbr')}",
            }
        )
        return

    store_name = entry.get("store_name")
    if not store_name:
        plan["skipped"].append({"type": "CH Store", "reason": "missing store_name", "entry": entry})
        return

    # Unique-by-(store_name, company) matches ch_store._validate_unique_store_name.
    existing = frappe.db.get_value(
        "CH Store",
        {"store_name": store_name, "company": company},
        "name",
    )
    if existing:
        # HEAL missing zone / city links on stores that were seeded during an
        # earlier broken migrate (e.g. the pre-fix v27 run where CH Store Zone
        # inserts failed on ch_hub_bin_type, so stores were created with
        # zone=NULL and then locked in as "already exists" on the retry).
        # Only fills BLANK fields — never overrides an operator-set value.
        if not plan["dry_run"]:
            current = frappe.db.get_value(
                "CH Store", existing,
                ["zone", "city", "warehouse"],
                as_dict=True,
            ) or {}
            healed_fields = {}

            target_zone = entry.get("zone")
            if (
                not (current.get("zone") or "").strip()
                and target_zone
                and frappe.db.exists("CH Store Zone", target_zone)
            ):
                # Confirm the zone actually belongs to this company/city before
                # linking, so we never bind a store to a foreign zone if the
                # PK happens to collide (autoname is field:zone_name → PKs are
                # globally unique, but the extra guard is cheap).
                zone_row = frappe.db.get_value(
                    "CH Store Zone", target_zone,
                    ["company", "city"], as_dict=True,
                ) or {}
                if zone_row.get("company") == company:
                    healed_fields["zone"] = target_zone

            target_city = _resolve_city_ref(entry, plan, "CH Store", store_name)
            if not (current.get("city") or "").strip() and target_city:
                healed_fields["city"] = target_city

            if healed_fields:
                frappe.db.set_value(
                    "CH Store", existing, healed_fields,
                    update_modified=False,
                )
                plan["updated"].append(
                    {"type": "CH Store", "name": existing, "healed": healed_fields}
                )

            if not (current.get("warehouse") or "").strip():
                from ch_item_master.ch_core.warehouse_geo import provision_store_warehouse

                provision_store_warehouse(existing)
        plan["skipped"].append({"type": "CH Store", "name": existing, "reason": "already exists"})
        return

    warehouse = None
    warehouse_base = entry.get("warehouse_base")
    if warehouse_base:
        target_warehouse = _warehouse_target_name(warehouse_base, company)
        if frappe.db.exists("Warehouse", target_warehouse):
            warehouse = target_warehouse

    city = _resolve_city_ref(entry, plan, "CH Store", store_name)

    # Zone PK is deterministic (autoname = field:zone_name) and zones are
    # imported before stores, so a missing zone here is a real gap — drop
    # the link so the store still seeds, and record the follow-up.
    zone = entry.get("zone")
    if zone and not frappe.db.exists("CH Store Zone", zone):
        plan["manual_followups"].append(
            {
                "type": "CH Store",
                "name": store_name,
                "reason": f"zone {zone!r} does not exist on target site — store seeded without zone",
            }
        )
        zone = None

    values = {k: entry.get(k) for k in _STORE_FIELDS}
    values["city"] = city
    values["zone"] = zone
    values["company"] = company
    values["warehouse"] = warehouse

    # Fail-soft on missing Link targets that we do NOT seed ourselves.
    # ``territory`` (Territory) and ``branch`` (Branch) are ordinarily
    # provisioned by ERPNext / HR, but on a fresh site they may be absent.
    # If the seed value points at a row that doesn't exist, drop the link
    # (record a follow-up) so the store row still seeds — same pattern
    # used for missing zones above.
    if values.get("territory") and not frappe.db.exists("Territory", values["territory"]):
        plan["manual_followups"].append(
            {
                "type": "CH Store",
                "name": store_name,
                "reason": f"territory {values['territory']!r} missing on target site — store seeded without territory",
            }
        )
        values["territory"] = None
    if values.get("branch") and not frappe.db.exists("Branch", values["branch"]):
        plan["manual_followups"].append(
            {
                "type": "CH Store",
                "name": store_name,
                "reason": f"branch {values['branch']!r} missing on target site — store seeded without branch",
            }
        )
        values["branch"] = None

    plan["to_create"].append(
        {
            "type": "CH Store",
            "store_name": store_name,
            "values": values,
            "auto_provision_warehouse": not bool(warehouse),
        }
    )
    if not plan["dry_run"]:
        doc = frappe.new_doc("CH Store")
        for k, v in values.items():
            if v is not None:
                setattr(doc, k, v)
        doc.insert(ignore_permissions=True)
        if not warehouse:
            from ch_item_master.ch_core.warehouse_geo import provision_store_warehouse

            provision_store_warehouse(doc.name)
        # after_insert / provision_store_warehouse take care of bins + POS Profile.


def import_location_hierarchy(
    data: dict | str,
    *,
    dry_run: bool = True,
    company_map: dict | None = None,
) -> dict:
    """Import a location-hierarchy snapshot.

    Parameters
    ----------
    data : dict | str
        Either a parsed payload (dict) or a filesystem path to a JSON
        dump produced by :func:`export_to_file`.
    dry_run : bool, default True
        When True, records what WOULD be created / skipped and does
        not mutate the database.  Flip to False after reviewing the
        plan.
    company_map : dict, optional
        ``{source_company_abbr: target_company_name}`` overrides for
        sites where the target Company is named differently from the
        source.
    """
    if isinstance(data, str):
        with open(data, "r", encoding="utf-8") as fh:
            data = json.load(fh)

    version = data.get("schema_version")
    if version and version > SEED_SCHEMA_VERSION:
        frappe.throw(
            f"Seed file schema_version={version} is newer than this build "
            f"({SEED_SCHEMA_VERSION}). Upgrade ch_item_master before importing."
        )

    plan: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "source_site": data.get("source_site"),
        "target_site": frappe.local.site,
        "schema_version": version,
        "to_create": [],
        "updated": [],
        "skipped": [],
        "manual_followups": [],
        "errors": [],
    }

    # Fresh-site guard: several Link targets used downstream are ordinarily
    # created by ERPNext's Setup Wizard or by first-Company insert. On a
    # brand-new site the seeder runs BEFORE that, so we materialise the
    # bare-minimum masters here — idempotent, no-op on established sites.
    if not plan["dry_run"]:
        _ensure_dependent_masters(plan)

    # Dependency order: Company → State → City → Zone → Store.
    # Each _upsert_* records to plan and (when not dry-run) writes.
    for entry in data.get("companies", []):
        try:
            _upsert_company(entry, plan, company_map)
        except Exception as e:  # noqa: BLE001
            plan["errors"].append({"type": "Company", "entry": entry, "error": str(e)})
            frappe.log_error(frappe.get_traceback(), f"seed Company {entry.get('company_name')}")

    for entry in data.get("states", []):
        try:
            _upsert_state(entry, plan)
        except Exception as e:  # noqa: BLE001
            plan["errors"].append({"type": "CH State", "entry": entry, "error": str(e)})
            frappe.log_error(frappe.get_traceback(), f"seed CH State {entry.get('state_name')}")

    for entry in data.get("cities", []):
        try:
            _upsert_city(entry, plan)
        except Exception as e:  # noqa: BLE001
            plan["errors"].append({"type": "CH City", "entry": entry, "error": str(e)})
            frappe.log_error(frappe.get_traceback(), f"seed CH City {entry.get('city_name')}")

    for entry in data.get("zones", []):
        try:
            _upsert_zone(entry, plan, company_map)
        except Exception as e:  # noqa: BLE001
            plan["errors"].append({"type": "CH Store Zone", "entry": entry, "error": str(e)})
            frappe.log_error(frappe.get_traceback(), f"seed CH Store Zone {entry.get('zone_name')}")

    for entry in data.get("stores", []):
        try:
            _upsert_store(entry, plan, company_map)
        except Exception as e:  # noqa: BLE001
            plan["errors"].append({"type": "CH Store", "entry": entry, "error": str(e)})
            frappe.log_error(frappe.get_traceback(), f"seed CH Store {entry.get('store_name')}")

    if not dry_run:
        frappe.db.commit()

    plan["summary"] = {
        "to_create": len(plan["to_create"]),
        "updated": len(plan["updated"]),
        "skipped": len(plan["skipped"]),
        "manual_followups": len(plan["manual_followups"]),
        "errors": len(plan["errors"]),
    }
    return plan


def import_from_file(in_path: str, apply: bool = False, company_map_json: str | None = None) -> dict:
    """CLI-friendly wrapper — reads a file, prints a plan.

    ``apply=False`` means DRY-RUN (default and safe).  Only when
    ``apply=True`` does the function actually mutate the site.
    """
    if not in_path or not os.path.exists(in_path):
        frappe.throw(f"Seed file not found: {in_path}")
    company_map = json.loads(company_map_json) if company_map_json else None
    return import_location_hierarchy(in_path, dry_run=not apply, company_map=company_map)


def baseline_seed_path() -> str | None:
    """Return the shipped baseline seed path, if present in this app build."""
    candidate = os.path.join(frappe.get_app_path("ch_item_master"), BASELINE_RELATIVE_PATH)
    return candidate if os.path.exists(candidate) else None


def seed_baseline_location_hierarchy() -> dict:
    """Idempotently import the shipped location hierarchy baseline.

    This is safe to call from ``after_migrate``. Existing rows are skipped by
    natural key; missing derived hubs / store Sellable warehouses are created.
    """
    path = baseline_seed_path()
    if not path:
        msg = (
            "location hierarchy baseline seed file not found at "
            f"ch_item_master/{BASELINE_RELATIVE_PATH}; skipping"
        )
        print(msg)
        return {"skipped": True, "reason": msg}

    result = import_from_file(path, apply=True)
    summary = result.get("summary") or {}
    print(
        "seed_baseline_location_hierarchy: "
        f"created={summary.get('to_create', 0)} "
        f"updated={summary.get('updated', 0)} "
        f"skipped={summary.get('skipped', 0)} "
        f"manual_followups={summary.get('manual_followups', 0)} "
        f"errors={summary.get('errors', 0)}"
    )
    for row in (result.get("updated") or [])[:15]:
        print(f"  healed: {row}")
    # "already exists" skips are the idempotent steady state — anything else
    # (unresolvable company/city, errors) means rows were NOT seeded and must
    # be surfaced loudly, not buried in the Error Log.
    noteworthy = [s for s in (result.get("skipped") or []) if s.get("reason") != "already exists"]
    for row in noteworthy[:15]:
        print(f"  WARNING skipped: {row}")
    for row in (result.get("manual_followups") or [])[:15]:
        print(f"  WARNING follow-up: {row}")
    for row in (result.get("errors") or [])[:15]:
        print(f"  ERROR: {row.get('type')} {row.get('entry', {}).get('store_name') or row.get('entry', {}).get('zone_name') or ''} — {row.get('error')}")
    return result
