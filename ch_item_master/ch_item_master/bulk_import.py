# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Bulk Import Lifecycle Auto-Activation
======================================

Industry pattern (SAP / Oracle / Dynamics): historic / migration data loaded
via Data Import or the Migration toolkit bypasses the governance workflow
and lands directly in an "Active" lifecycle state. Day-2 records created
through the UI continue to flow Draft -> Pending Review -> Active so the
approval workflow stays meaningful.

Frappe surfaces two trustworthy signals during a Data Import:
  * ``frappe.flags.in_import`` is set to True for the duration of the import
  * ``doc.flags.in_import`` is set on every imported document

We honour either flag and pre-fill the lifecycle / PLM / approval fields to
their "ready to transact" values *only when those fields are still empty or
at the form default*. Values that the IT team explicitly put in the CSV are
always preserved.
"""

from __future__ import annotations

import frappe


# ── per-doctype "active" presets ────────────────────────────────────────────
# Each entry maps fieldname -> value to apply when the field is unset or at
# its Draft/NPI default during a bulk import.
_ACTIVE_DEFAULTS: dict[str, dict[str, str]] = {
	"Item": {
		"ch_lifecycle_status": "Active",
		"ch_plm_status": "Active Production",
		"ch_approval_status": "Approved",
	},
	"CH Category": {
		"lifecycle_status": "Active",
	},
	"CH Sub Category": {
		"lifecycle_status": "Active",
	},
}

# Values that we are willing to overwrite (i.e. the form defaults that block
# transactions). Anything else the user typed in the CSV is left alone.
_OVERWRITABLE: dict[str, set[str]] = {
	"ch_lifecycle_status": {"", "Draft", "Pending Review"},
	"ch_plm_status": {"", "NPI", "Under Review", "Sample Testing"},
	"ch_approval_status": {"", "Draft", "Submitted for Review"},
	"lifecycle_status": {"", "Draft", "Pending Review"},
}


def _is_bulk_import(doc) -> bool:
	"""True when the document is being created via Data Import / migration."""
	if getattr(doc.flags, "in_import", False):
		return True
	if getattr(frappe.flags, "in_import", False):
		return True
	# bench --site ... migrate / install also legitimately seeds masters
	if getattr(frappe.flags, "in_install", False):
		return True
	if getattr(frappe.flags, "in_migrate", False):
		return True
	return False


def apply_active_defaults(doc, method=None):
	"""Hooked as ``before_insert`` for Item / CH Category / CH Sub Category.

	No-op for UI / API creates so the governance workflow keeps gating
	day-2 records.
	"""
	if not _is_bulk_import(doc):
		return

	presets = _ACTIVE_DEFAULTS.get(doc.doctype)
	if not presets:
		return

	for fieldname, target in presets.items():
		if not hasattr(doc, fieldname):
			continue
		current = (getattr(doc, fieldname, None) or "").strip()
		if current in _OVERWRITABLE.get(fieldname, {""}):
			doc.set(fieldname, target)


# ── one-shot backfill for records loaded *before* this hook existed ─────────
@frappe.whitelist()
def backfill_active_defaults() -> dict:
	"""Promote stuck Draft / NPI master records to Active.

	Safe to re-run. Only updates rows that are still at the form default,
	never overrides a record someone has explicitly Reviewed / Rejected.
	Returns a per-doctype count of rows touched.
	"""
	frappe.only_for("System Manager")

	results: dict[str, int] = {}

	def _bulk_update(table: str, field: str, target: str, stuck: tuple[str, ...]) -> int:
		if not frappe.db.table_exists(table):
			return 0
		if not frappe.db.has_column(table.removeprefix("tab"), field):
			return 0
		placeholders = ", ".join(["%s"] * len(stuck))
		count = frappe.db.sql(
			f"SELECT COUNT(*) FROM `{table}` "
			f"WHERE COALESCE(`{field}`, '') IN ({placeholders})",
			stuck,
		)[0][0]
		if count:
			frappe.db.sql(
				f"UPDATE `{table}` SET `{field}` = %s "
				f"WHERE COALESCE(`{field}`, '') IN ({placeholders})",
				(target, *stuck),
			)
		return int(count)

	results["Item.ch_lifecycle_status"] = _bulk_update(
		"tabItem", "ch_lifecycle_status", "Active", ("", "Draft")
	)
	results["Item.ch_plm_status"] = _bulk_update(
		"tabItem", "ch_plm_status", "Active Production", ("", "NPI")
	)
	results["Item.ch_approval_status"] = _bulk_update(
		"tabItem", "ch_approval_status", "Approved", ("", "Draft")
	)
	results["CH Category.lifecycle_status"] = _bulk_update(
		"tabCH Category", "lifecycle_status", "Active", ("", "Draft")
	)
	results["CH Sub Category.lifecycle_status"] = _bulk_update(
		"tabCH Sub Category", "lifecycle_status", "Active", ("", "Draft")
	)

	frappe.db.commit()
	return results
