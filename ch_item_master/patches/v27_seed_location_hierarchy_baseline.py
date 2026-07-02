"""v27: seed the CH location hierarchy from the shipped baseline JSON.

Ships the golden baseline dataset (38 states, 794 cities, 9 zones, 49
stores as of 2026-07-02) into the site so that every teammate — and
every fresh install — starts with the same canonical geography master.

Idempotent by natural key:
  * CH State: matched by state_name (PK).
  * CH City : matched by (city_name, state) — handles the state-code
              autoname suffix transparently.
  * CH Store Zone: matched by zone_name.
  * CH Store: matched by (store_name, company).

Existing rows are LEFT ALONE (skipped, not updated). This makes the
patch safe for sites that already have their location master —
running v27 on such a site will simply report "skipped" for every
row.

Fresh sites, on the other hand, get the whole hierarchy in one
migrate. The seed file lives at
``ch_item_master/data/seed/location_hierarchy_ch_baseline.json`` and
is version-controlled alongside the app so every environment resolves
to the same canonical data.

Design parity:
  * SAP SLT initial-load package — ships the master data snapshot
    alongside the transport request.
  * Oracle Functional Setup Manager configuration package.
  * D365 Data Management Framework baseline data project.
"""

from __future__ import annotations

from ch_item_master.ch_core.location_hierarchy_seed import seed_baseline_location_hierarchy


def execute():
	result = seed_baseline_location_hierarchy()
	summary = result.get("summary") or {}
	print(
		"v27_seed_location_hierarchy_baseline: "
		f"created={summary.get('to_create', 0)} "
		f"skipped={summary.get('skipped', 0)} "
		f"manual_followups={summary.get('manual_followups', 0)} "
		f"errors={summary.get('errors', 0)}"
	)

	# Errors are already captured via frappe.log_error inside the importer,
	# but surface a compact hint here so the operator running migrate can see
	# them without opening the Error Log doctype.
	for err in (result.get("errors") or [])[:20]:
		print(f"  error: {err.get('type')} {err.get('entry', {}).get('name') or ''} — {err.get('error')}")
