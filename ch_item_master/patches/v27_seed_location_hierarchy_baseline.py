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

import os

import frappe

from ch_item_master.ch_core.location_hierarchy_seed import import_from_file


SEED_RELATIVE_PATH = os.path.join("data", "seed", "location_hierarchy_ch_baseline.json")


def _seed_path() -> str | None:
	app_path = frappe.get_app_path("ch_item_master")
	candidate = os.path.join(app_path, SEED_RELATIVE_PATH)
	return candidate if os.path.exists(candidate) else None


def execute():
	seed_path = _seed_path()
	if not seed_path:
		# Ship the JSON with the app; if a teammate has stripped it, log
		# and no-op rather than crash the migrate.
		print(
			"v27_seed_location_hierarchy_baseline: seed file not found at "
			f"ch_item_master/{SEED_RELATIVE_PATH}; skipping."
		)
		return

	result = import_from_file(seed_path, apply=True)
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
