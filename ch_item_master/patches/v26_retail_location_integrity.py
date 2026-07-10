"""v26: harden retail location / hub mappings.

Repairs drift introduced by older topology passes:
  * store Sellable warehouses accidentally used as zone hubs
  * shared city hubs stamped to one arbitrary zone
  * missing hub metadata on CH Store Zone.source_warehouse

Idempotent. Safe to re-run.
"""

from __future__ import annotations

import frappe

from ch_item_master.ch_core.location_hierarchy import repair_retail_location_integrity


def _ensure_prerequisite_custom_fields():
	"""Make sure Warehouse custom fields exist BEFORE reading them back.

	Patch ordering pitfall (see /memories/repo/patch-custom-field-ordering.md):
	this patch lives in ``[post_model_sync]``, which runs *before* the
	``after_migrate`` hook that calls ``create_ch_custom_fields``. On a
	fresh install (or on a site that predates the ``ch_hub_bin_type``
	field) ``repair_retail_location_integrity`` -> ``_warehouse_row`` ->
	``SELECT ..., ch_hub_bin_type, ... FROM tabWarehouse`` runs before the
	column exists and blows up with
	``(1054, "Unknown column 'ch_hub_bin_type' in 'SELECT'")``.

	Applying the shared ``CUSTOM_FIELDS`` here is idempotent (upstream
	``create_custom_fields`` skips fields that already match) and triggers
	the DDL that adds any missing columns. Same guard already used by
	``v27_seed_location_hierarchy_baseline``.
	"""
	try:
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		from ch_item_master.constants.custom_fields import CUSTOM_FIELDS

		create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
		frappe.db.commit()
	except Exception:
		# Never let the guard itself break migrate — if this fails, the
		# repair pass below will surface a clearer per-row error and can
		# be re-run by hand.
		frappe.log_error(
			frappe.get_traceback(),
			"v26_retail_location_integrity: prerequisite custom fields",
		)


def execute():
	_ensure_prerequisite_custom_fields()
	result = repair_retail_location_integrity()
	print(
		"v26_retail_location_integrity: "
		f"fixed={len(result.get('fixed') or [])} "
		f"warnings={len(result.get('warnings') or [])}"
	)
	for line in (result.get("fixed") or [])[:50]:
		print(f"  fixed: {line}")
	for line in (result.get("warnings") or [])[:50]:
		print(f"  warning: {line}")
