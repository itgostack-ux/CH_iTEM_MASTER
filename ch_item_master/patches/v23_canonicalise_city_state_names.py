"""v23 — Canonicalise CH State and CH City names.

Background
----------
``CH City.autoname`` was ``format:{state}-{city_name}`` which silently
compounded when ``state`` itself held a non-canonical value. Production
ended up with rows like::

    CH State.name = "33 Tamil Nadu"     (state_code stuffed into name)
    CH City.name  = "Tamil Nadu-Tamil Nadu-…-Bestbuy Mobiles Pvt Ltd-Chennai"

These names then leaked into the Location Hierarchy UI, the new
"Stores by Location" report, and every Address / Store row that linked
them.

This patch:

* Splits state rows whose ``name`` is "<code> <state_name>" into clean
  ``name = state_name`` and ``state_code = code`` and renames the doc
  (Frappe re-points every FK automatically).
* Renames any CH City row whose ``name`` ≠ ``city_name`` to its
  ``city_name`` (with a numeric suffix only on collision).

The patch is idempotent and safe to re-run.
"""

from __future__ import annotations

import re

import frappe


_STATE_CODE_PREFIX_RE = re.compile(r"^\s*(?P<code>[A-Za-z0-9]{1,3})\s+(?P<rest>.+?)\s*$")


def _safe_rename(doctype: str, old: str, new: str) -> str | None:
	"""Rename ``old`` -> ``new`` for ``doctype``; on collision, suffix ``-2``.

	Returns the final name actually used, or ``None`` if no rename happened.
	"""
	if old == new:
		return None
	if not frappe.db.exists(doctype, old):
		return None
	final = new
	suffix = 2
	while final != old and frappe.db.exists(doctype, final):
		final = f"{new}-{suffix}"
		suffix += 1
		if suffix > 50:
			frappe.log_error(
				title="v23_canonicalise_city_state_names",
				message=f"Could not find a free name to rename {doctype} {old!r} → {new!r}",
			)
			return None
	if final == old:
		return None
	try:
		frappe.rename_doc(doctype, old, final, force=True, merge=False)
		return final
	except Exception:
		frappe.log_error(
			title="v23_canonicalise_city_state_names rename failed",
			message=f"{doctype} {old!r} → {final!r}\n{frappe.get_traceback()}",
		)
		return None


def _canonicalise_states() -> int:
	"""Fix rows like ``CH State.name = "33 Tamil Nadu"``."""
	count = 0
	rows = frappe.db.sql(
		"""
		SELECT name, state_name, state_code, country
		FROM `tabCH State`
		""",
		as_dict=True,
	)
	for row in rows:
		current = row.name or ""
		desired_name = (row.state_name or "").strip().title()
		desired_code = (row.state_code or "").strip().upper()

		# Detect the "<code> <name>" pattern stuffed into name and split it.
		m = _STATE_CODE_PREFIX_RE.match(current)
		if m and not desired_code:
			# State row was created with name="33 Tamil Nadu", state_code blank.
			code_part = m.group("code").upper()
			rest_part = m.group("rest").strip().title()
			frappe.db.set_value("CH State", current, "state_code", code_part)
			frappe.db.set_value("CH State", current, "state_name", rest_part)
			desired_name = rest_part
			desired_code = code_part
		elif current != desired_name and desired_name:
			# Otherwise just align name with state_name if it drifted.
			pass

		if desired_name and current != desired_name:
			renamed = _safe_rename("CH State", current, desired_name)
			if renamed:
				count += 1

	return count


def _canonicalise_cities() -> int:
	"""Rename CH City rows whose ``name`` no longer equals ``city_name``."""
	count = 0
	rows = frappe.db.sql(
		"""
		SELECT name, city_name
		FROM `tabCH City`
		WHERE city_name IS NOT NULL AND city_name != ''
		""",
		as_dict=True,
	)
	for row in rows:
		desired = (row.city_name or "").strip().title()
		if desired and row.name != desired:
			renamed = _safe_rename("CH City", row.name, desired)
			if renamed:
				count += 1
	return count


def _switch_city_autoname() -> bool:
	"""Replace the compounding autoname format with a stable one.

	``format:{city_name}`` cannot compound on re-save and matches the
	cleaned-up keys produced above. Disambiguation by state is no longer
	needed in this single-country (India) deployment; should that ever
	change, the right fix is a composite key like ``{state_code}-{city_name}``
	driven from the CLEAN state_code, not from a free-form state field.
	"""
	current = frappe.db.get_value("DocType", "CH City", "autoname")
	if current == "format:{city_name}":
		return False
	frappe.db.set_value("DocType", "CH City", "autoname", "format:{city_name}")
	frappe.clear_cache(doctype="CH City")
	return True


def execute():
	# Order matters: clean states first so that any downstream re-use of
	# ``state`` in city names (e.g. the report header) renders correctly.
	states_fixed = _canonicalise_states()
	cities_fixed = _canonicalise_cities()
	autoname_changed = _switch_city_autoname()
	frappe.db.commit()
	print(
		f"v23_canonicalise_city_state_names: "
		f"states_renamed={states_fixed}, cities_renamed={cities_fixed}, "
		f"autoname_changed={autoname_changed}"
	)
