"""v24 — Mop up ``-2`` suffix collisions left by v23.

v23 canonicalised ``CH State`` / ``CH City`` names but used a numeric
``-N`` suffix whenever a rename target was temporarily occupied during
the run. After the dust settled the canonical names are now free again
(or, in some cases, both rows survive and represent the same real
entity). This patch:

* For every ``Foo-N`` row whose canonical ``Foo`` no longer exists,
  rename ``Foo-N`` → ``Foo`` and also strip the suffix from the
  ``state_name`` / ``city_name`` field so display stays consistent.
* For every ``Foo-N`` row whose canonical ``Foo`` DOES exist (true
  duplicate), MERGE ``Foo-N`` into ``Foo`` so every FK is re-pointed
  to the canonical row and the duplicate is dropped.
* For city rows whose ``city_name`` itself still contains the legacy
  compound junk (``-…-Bestbuy Mobiles Pvt Ltd-Mumbai``), peel the
  final segment after the last ``-`` as the real city, rename/merge,
  and overwrite ``city_name`` with the clean value.

Idempotent: re-running on already-clean data is a no-op.
"""

from __future__ import annotations

import re

import frappe


_SUFFIX_RE = re.compile(r"^(?P<base>.+?)-(?P<n>\d+)$")


def _canonical_base(name: str) -> str | None:
	"""Return the base name without a trailing ``-N``, else ``None``."""
	m = _SUFFIX_RE.match(name or "")
	return m.group("base") if m else None


def _peel_compound_city(city_name: str) -> str | None:
	"""For values like ``--Bestbuy Mobiles Pvt Ltd-Mumbai`` return ``Mumbai``.

	We only peel when the input is clearly a compound (contains at least
	one ``-``) and the last segment looks like a real city token (length
	≥ 2 and not all digits). Otherwise return ``None`` to leave the row
	untouched.
	"""
	if not city_name or "-" not in city_name:
		return None
	last = city_name.rsplit("-", 1)[-1].strip().title()
	if len(last) < 2 or last.isdigit():
		return None
	return last


def _rename_or_merge(doctype: str, old: str, new: str) -> str:
	"""Rename ``old`` → ``new``; if ``new`` already exists, merge into it.

	Returns one of ``"renamed"``, ``"merged"``, ``"skipped"`` or
	``"failed"`` for logging.
	"""
	if old == new:
		return "skipped"
	if not frappe.db.exists(doctype, old):
		return "skipped"
	target_exists = bool(frappe.db.exists(doctype, new))
	try:
		frappe.rename_doc(doctype, old, new, merge=target_exists, force=True)
		return "merged" if target_exists else "renamed"
	except Exception:
		frappe.log_error(
			title=f"v24_finalize_city_state_canonicalisation",
			message=f"{doctype} {old!r} → {new!r} (merge={target_exists})\n{frappe.get_traceback()}",
		)
		return "failed"


def _fix_states() -> dict[str, int]:
	stats = {"renamed": 0, "merged": 0, "field_cleaned": 0, "skipped": 0, "failed": 0}
	rows = frappe.db.sql(
		"SELECT name, state_name FROM `tabCH State`", as_dict=True
	)
	for row in rows:
		base = _canonical_base(row.name)
		clean_state_name = (row.state_name or "").strip()
		# Strip a trailing -N from state_name too (leaked there by v23).
		sname_base = _canonical_base(clean_state_name)
		if sname_base:
			frappe.db.set_value("CH State", row.name, "state_name", sname_base)
			stats["field_cleaned"] += 1
			clean_state_name = sname_base
		if not base:
			continue
		result = _rename_or_merge("CH State", row.name, base)
		stats[result] = stats.get(result, 0) + 1
		# Post-rename: ensure the (now-canonical) row also has clean state_name.
		if result in ("renamed", "merged"):
			frappe.db.set_value("CH State", base, "state_name", base)
	return stats


def _fix_cities() -> dict[str, int]:
	stats = {
		"renamed": 0, "merged": 0, "peeled": 0,
		"field_cleaned": 0, "skipped": 0, "failed": 0,
	}
	rows = frappe.db.sql(
		"SELECT name, city_name FROM `tabCH City`", as_dict=True
	)
	for row in rows:
		current = row.name
		current_city_name = (row.city_name or "").strip()

		# Step 1: if the city_name itself is compound junk (e.g.
		# "--Bestbuy Mobiles Pvt Ltd-Mumbai"), peel the last token.
		peeled = _peel_compound_city(current_city_name)
		if peeled:
			frappe.db.set_value("CH City", current, "city_name", peeled)
			stats["peeled"] += 1
			current_city_name = peeled

		# Step 2: strip a trailing -N from city_name (leaked there by v23).
		cname_base = _canonical_base(current_city_name)
		if cname_base:
			frappe.db.set_value("CH City", current, "city_name", cname_base)
			stats["field_cleaned"] += 1
			current_city_name = cname_base

		# Step 3: align name with the (now clean) city_name.
		if current_city_name and current != current_city_name:
			result = _rename_or_merge("CH City", current, current_city_name)
			stats[result] = stats.get(result, 0) + 1
	return stats


def execute():
	st = _fix_states()
	ct = _fix_cities()
	frappe.db.commit()
	print(f"v24_finalize_city_state_canonicalisation: states={st}, cities={ct}")
