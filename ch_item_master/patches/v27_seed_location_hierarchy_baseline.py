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

import frappe

from ch_item_master.ch_core.location_hierarchy_seed import seed_baseline_location_hierarchy


def _ensure_prerequisite_custom_fields():
	"""Make sure Warehouse custom fields exist BEFORE seeding CH Store Zones / Stores.

	Patch ordering pitfall: this patch lives in ``[post_model_sync]``, which
	runs *before* the ``after_migrate`` hook that calls
	``create_ch_custom_fields``.  On a fresh install (or on a site that
	predates the ``ch_hub_bin_type`` field / the ``Demo`` bin type) the
	seed would then fire ``CH Store Zone.validate`` -> ``_warehouse_row``
	-> ``SELECT ..., ch_hub_bin_type, ... FROM tabWarehouse`` before the
	column exists, and ``CH Store.after_insert`` -> ``ensure_store_bins``
	would try to set ``ch_bin_type="Demo"`` before that option was added
	to the Select field.  Both failed with (1054) / Select-option errors.

	Applying the shared ``CUSTOM_FIELDS`` here is idempotent (upstream
	``create_custom_fields`` skips fields that already match), triggers the
	DDL that adds any missing columns, and updates Select options in place.
	"""
	try:
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		from ch_item_master.constants.custom_fields import CUSTOM_FIELDS

		create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
		frappe.db.commit()
	except Exception:
		# Never let the guard itself break migrate — if this fails, the seed
		# will surface a clearer per-row error below and can be re-run.
		frappe.log_error(
			frappe.get_traceback(),
			"v27_seed_location_hierarchy_baseline: prerequisite custom fields",
		)


def execute():
	_ensure_prerequisite_custom_fields()
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
