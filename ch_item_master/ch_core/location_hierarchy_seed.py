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

* Companies are NOT exported (they carry chart-of-accounts, taxes,
  and territory data that must be provisioned per site).  Instead
  we record each entity's company as an *abbreviation* and require
  the target site to already have a Company with the same abbr, OR
  provide a ``company_map`` at import time.

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


# Format version — bump when the schema of the JSON changes.  Import
# refuses to load a file whose version is newer than what this module
# knows about.
SEED_SCHEMA_VERSION = 1

# Fields captured per doctype.  We deliberately DON'T dump audit
# columns (creation, modified, owner, _user_tags, _assign, _liked_by,
# _comments, docstatus, idx, name) — those are site-local metadata.
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


def _company_abbr(company: str) -> str:
    if not company:
        return ""
    return frappe.db.get_value("Company", company, "abbr") or ""


def _company_from_abbr(abbr: str) -> str | None:
    if not abbr:
        return None
    return frappe.db.get_value("Company", {"abbr": abbr}, "name")


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


def _export_states() -> list[dict]:
    rows = frappe.get_all("CH State", fields=list(_STATE_FIELDS), order_by="state_name")
    return [dict(r) for r in rows]


def _export_cities() -> list[dict]:
    rows = frappe.get_all(
        "CH City", fields=list(_CITY_FIELDS), order_by="city_name"
    )
    return [dict(r) for r in rows]


def _export_zones(company_filter: Iterable[str] | None) -> list[dict]:
    filters: dict[str, Any] = {}
    if company_filter:
        filters["company"] = ["in", list(company_filter)]
    rows = frappe.get_all(
        "CH Store Zone",
        filters=filters,
        fields=[*_ZONE_FIELDS, "company", "source_warehouse"],
        order_by="company, zone_name",
    )
    out = []
    for r in rows:
        abbr = _company_abbr(r.company)
        out.append(
            {
                **{k: r.get(k) for k in _ZONE_FIELDS},
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
    rows = frappe.get_all(
        "CH Store",
        filters=filters,
        fields=[*_STORE_FIELDS, "company", "warehouse"],
        order_by="company, store_code",
    )
    out = []
    for r in rows:
        abbr = _company_abbr(r.company)
        out.append(
            {
                **{k: r.get(k) for k in _STORE_FIELDS},
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
        "states": _export_states(),
        "cities": _export_cities(),
        "zones": _export_zones(company_filter),
        "stores": _export_stores(company_filter),
    }
    payload["counts"] = {k: len(payload[k]) for k in ("states", "cities", "zones", "stores")}
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
    if existing:
        plan["skipped"].append({"type": "CH City", "name": existing, "reason": "already exists"})
        return
    plan["to_create"].append({"type": "CH City", "city_name": city_name, "values": {k: entry.get(k) for k in _CITY_FIELDS}})
    if not plan["dry_run"]:
        doc = frappe.new_doc("CH City")
        for k in _CITY_FIELDS:
            if entry.get(k) is not None:
                setattr(doc, k, entry.get(k))
        doc.insert(ignore_permissions=True)


def _resolve_warehouse_for_seed(base_name: str, company: str, plan: dict, purpose: str) -> str | None:
    """Return a target-site warehouse name for a seed reference.

    We do NOT create warehouses here — Zones and Stores accept a
    warehouse ref that must already exist.  If the target site is
    missing the warehouse we RECORD it as a manual follow-up and
    return None; the caller then skips / defers the parent record.
    """
    if not base_name:
        return None
    target = _warehouse_target_name(base_name, company)
    if frappe.db.exists("Warehouse", target):
        return target
    plan["manual_followups"].append(
        {
            "type": "Warehouse",
            "base_name": base_name,
            "company": company,
            "purpose": purpose,
            "reason": "warehouse does not exist on target site — create it first",
        }
    )
    return None


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
    city = entry.get("city")
    if not (zone_name and city):
        plan["skipped"].append({"type": "CH Store Zone", "reason": "missing zone_name/city", "entry": entry})
        return

    existing = frappe.db.get_value(
        "CH Store Zone",
        {"zone_name": zone_name, "company": company, "city": city},
        "name",
    )
    if existing:
        plan["skipped"].append({"type": "CH Store Zone", "name": existing, "reason": "already exists"})
        return

    source_wh = _resolve_warehouse_for_seed(
        entry.get("source_warehouse_base"),
        company,
        plan,
        purpose=f"CH Store Zone {zone_name} source_warehouse",
    )

    values = {k: entry.get(k) for k in _ZONE_FIELDS}
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
        plan["skipped"].append({"type": "CH Store", "name": existing, "reason": "already exists"})
        return

    warehouse = _resolve_warehouse_for_seed(
        entry.get("warehouse_base"),
        company,
        plan,
        purpose=f"CH Store {store_name} sellable warehouse",
    )
    if not warehouse:
        # No warehouse → skip.  Store creation without a warehouse would
        # succeed but leave the store bin-less; better to force manual
        # remediation and re-run the import.
        plan["skipped"].append(
            {
                "type": "CH Store",
                "store_name": store_name,
                "reason": "no target warehouse — see manual_followups",
            }
        )
        return

    values = {k: entry.get(k) for k in _STORE_FIELDS}
    values["company"] = company
    values["warehouse"] = warehouse
    plan["to_create"].append({"type": "CH Store", "store_name": store_name, "values": values})
    if not plan["dry_run"]:
        doc = frappe.new_doc("CH Store")
        for k, v in values.items():
            if v is not None:
                setattr(doc, k, v)
        doc.insert(ignore_permissions=True)
        # after_insert takes care of bins + POS Profile.


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
        "skipped": [],
        "manual_followups": [],
        "errors": [],
    }

    # Dependency order: State → City → Zone → Store.
    # Each _upsert_* records to plan and (when not dry-run) writes.
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
