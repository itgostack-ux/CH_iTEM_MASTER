# CH Location Hierarchy — Golden Seed Datasets (Phase E)

This directory ships **reference datasets** for the retail geography
(State → City → Zone → Store).  They are consumed by:

* Fresh-site bootstrap  — new staging / DR / tenant sites start with
  the full production master data already present.
* Change control       — the JSON is committed to git and diffed
  across releases to detect unauthorised store openings, closures,
  or renames.
* Regression tests     — importers assert idempotency against a
  known-good baseline.

## Files

| File | Rows | Purpose |
|------|-----:|---------|
| `location_hierarchy_ch_baseline.json` | 892 (2 companies, 38 states, 794 cities, 9 zones, 49 stores) | Reference geography as of the initial GoFix production dump. Schema v2. |

## Usage

Export from a source site (replace the golden seed after major master-data changes):

```bash
bench --site erpnext.local ch-locations-export \
    --out apps/ch_item_master/ch_item_master/data/seed/location_hierarchy_ch_baseline.json
```

Then normalise the two run-specific fields for a clean git diff
(the shipped seed already has these set to their placeholder values):

```bash
python3 -c "import json,pathlib; p=pathlib.Path('apps/ch_item_master/ch_item_master/data/seed/location_hierarchy_ch_baseline.json'); d=json.loads(p.read_text()); d['source_site']='REFERENCE'; d['exported_at']='GOLDEN-BASELINE'; p.write_text(json.dumps(d,indent=2,sort_keys=True,default=str))"
```

Import into a fresh site (DRY-RUN by default — nothing writes until `--apply`):

```bash
# Preview what would happen
bench --site staging ch-locations-import \
    --in apps/ch_item_master/ch_item_master/data/seed/location_hierarchy_ch_baseline.json

# Actually write
bench --site staging ch-locations-import \
    --in apps/ch_item_master/ch_item_master/data/seed/location_hierarchy_ch_baseline.json \
    --apply
```

If the target site uses different company names, provide a mapping:

```bash
bench --site staging ch-locations-import \
    --in apps/ch_item_master/ch_item_master/data/seed/location_hierarchy_ch_baseline.json \
    --apply \
    --company-map '{"GSPL":"GoFix South Staging Pvt Ltd","BMPL":"BuyMe Staging Pvt Ltd"}'
```

## What the seed captures — and what it does NOT

**Captured** (seed-level entities that operators define):

* `Company` — MINIMAL identity only (name, abbr, country, currency);
  auto-created on import when no Company with the same abbr exists.
  ERPNext provisions the standard CoA on insert; GST registrations
  and tax setup remain a per-site follow-up.
* `CH State` — full state master (nationwide, cross-company)
* `CH City` — full city master (nationwide, cross-company)
* `CH Store Zone` — per-company zone with source hub warehouse ref
* `CH Store` — per-company store with sellable warehouse ref

**NOT captured** (auto-derived by existing hooks — do not export):

* Store Group / Zone Group / City Group warehouses — auto-materialised
  by `warehouse_geo.ensure_store_group` when a store is created
* Bin leaves (Sellable / Damaged / Demo / Buyback) — auto-materialised
  by `ch_store.after_insert → ensure_store_bins`
* Default Hub Bins (`Sellable-01`) — auto-materialised per hub by
  `location_hierarchy.backfill_default_hub_bins` (after_migrate);
  operators add extra bins (Sellable-02, Quarantine, …) from the
  Location Hierarchy page.
* Zone hub warehouses — materialised by the importer itself when the
  referenced hub is missing.

## Cross-site identity contract (schema v2)

Primary keys are DETERMINISTIC so every platform resolves to identical
identities:

* `Company` — matched by **abbr** (BMPL / GSPL); name follows the seed.
* `CH City` — PK is `{City}-{state_code}` (`Chennai-33`); patch
  `v28_canonicalise_city_pks` converges legacy sites, and the importer
  re-resolves all city references by natural key (`city_name` +
  `city_state`) so raw-PK drift can never break seeding again.
* `CH Store Zone` — PK = `zone_name`; `CH Store` — PK = `store_code`.
* Importer HEALS matched city rows: backfills missing `state`/`country`
  (fixes the "Unassigned Region" roll-up), merges stateless twins, and
  renames to the canonical PK.

## Idempotency contract

Re-running the import over a fully-seeded site is a no-op:

```
DRY-RUN: would create 0, skip 892, needs 0 manual follow-ups.
```

Rows that already exist match by natural key:

* `CH State` — `state_name` (PK)
* `CH City` — `(city_name, state)` (autoname is `{city_name}-{state_code}`)
* `CH Store Zone` — `(zone_name, company, city)`
* `CH Store` — `(store_name, company)` — matches `ch_store._validate_unique_store_name`

## Benchmark

Matches the "master-data movement between environments" pattern used
by SAP Landscape Transport (SLT), Oracle Fusion Configuration
Package, and Microsoft Dynamics 365 Data Management Framework
(DMF).  All three ship the source data as a versioned artifact,
enforce a dry-run preview, and match by natural key on import.
