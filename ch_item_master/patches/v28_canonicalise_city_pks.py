"""v28 — Canonicalise CH City primary keys to the state-aware autoname form.

Background
----------
``CHCity.autoname`` produces ``{City}-{state_code}`` (e.g. ``Chennai-33``,
``Bilaspur-CG``) so duplicate district names across states never collide and
the PK is a *deterministic function of the natural key* — the same on every
site. But v23/v24 (which cleaned up the older compound-name corruption)
canonicalised legacy rows to the *plain* ``city_name`` instead, so long-lived
sites carry ``Chennai`` while fresh sites seed ``Chennai-33``. The shipped
location-hierarchy baseline (v27) exports raw city PKs, so this divergence
made zone/store seeding fail on fresh sites with LinkValidationError.

This patch renames every CH City row whose ``name`` differs from the
canonical autoname form. ``frappe.rename_doc`` re-points every Link field
(CH Store.city, CH Store Zone.city, Warehouse.ch_city, CH Pincode.city,
Address.custom_ch_city, …) automatically. When the canonical target already
exists (true duplicate rows like ``Chennai`` + ``Chennai-33``), the legacy
row is MERGED into the canonical one.

Idempotent: re-running on already-canonical data is a no-op.

Display note: CH City sets ``title_field = city_name`` and
``show_title_field_in_link = 1``, so users keep seeing "Chennai" in link
fields regardless of the suffixed PK.
"""

from __future__ import annotations

import frappe


def canonical_city_pk(city_name: str, state: str | None) -> str | None:
	"""Compute the PK ``CHCity.autoname`` would assign — keep in sync with it."""
	city = (city_name or "").strip().title()
	if not city:
		return None
	state_token = None
	if state:
		state_code = (frappe.db.get_value("CH State", state, "state_code") or "").strip().upper()
		if state_code:
			state_token = state_code
		else:
			# Last-resort fallback if legacy states lack state_code.
			state_token = "".join(ch for ch in state.upper() if ch.isalnum())
	return f"{city}-{state_token}" if state_token else city


def execute():
	if not frappe.db.table_exists("CH City"):
		return

	rows = frappe.get_all("CH City", fields=["name", "city_name", "state"], limit_page_length=0)
	renamed = merged = unchanged = failed = 0

	for row in rows:
		target = canonical_city_pk(row.city_name, row.state)
		if not target or row.name == target:
			unchanged += 1
			continue
		merge = bool(frappe.db.exists("CH City", target))
		try:
			frappe.rename_doc("CH City", row.name, target, force=True, merge=merge)
		except Exception:
			failed += 1
			frappe.log_error(
				frappe.get_traceback(),
				f"v28 rename CH City {row.name!r} -> {target!r} (merge={merge})",
			)
			continue
		if merge:
			merged += 1
		else:
			renamed += 1
		done = renamed + merged
		if done % 50 == 0:
			frappe.db.commit()
			print(f"v28_canonicalise_city_pks: progress {done}/{len(rows)}")

	frappe.db.commit()
	print(
		"v28_canonicalise_city_pks: "
		f"renamed={renamed} merged={merged} unchanged={unchanged} failed={failed}"
	)
