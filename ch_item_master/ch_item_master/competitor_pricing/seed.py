"""Ship the competitor-price configuration as code, so a restore cannot lose it.

Sep-2026: the two working scrapers (Cashkr, Cashify), their adapter regexes
and 2,753 item→page links lived only in the database. They were built by hand
on 2026-08-12, never reached production, and the Sep-18 production restore
replaced them here too — the Buyback Price Planner went blank on every
competitor column, and nothing in the repo could bring it back.

`seed/sources.json` and `seed/item_links.json` are the exact rows that were
collecting successfully on Sep 12-13 (300 fetched · 300 priced · 0 blocked on
each). They are applied on every migrate, idempotently:

* a source that does not exist is created; one that does keeps every value an
  operator has set — only *blank* fields are filled in, so a regex fixed on
  the site is never reverted by a deploy;
* a link is created only for a (competitor, item) pair that has none, and
  only when the item exists;
* the non-behavioural settings (user agent, staleness window, failure limit,
  request cap) are filled in when unset.

What this deliberately does NOT do is switch collection on.
`competitor_collection_enabled` stays an operator decision: a migrate must
never start a site scraping third-party websites on its own.
"""

from __future__ import annotations

import json
import os
import re

import frappe
from frappe.utils import cint

#: The JSON files sit beside this module.
SEED_DIR = os.path.join(os.path.dirname(__file__), "seed")

#: Everything on CH Competitor Source that is configuration rather than run
#: state. `last_run_*` is what the collector writes and is never seeded.
SOURCE_CONFIG_FIELDS = (
	"base_url", "adapter", "url_template", "price_selector", "quote_selector",
	"price_regex", "default_condition_profile", "confidence", "request_delay_ms",
	"max_requests_per_run", "timeout_seconds", "user_agent", "notes",
)
#: Check fields: only meaningful at creation. A stored 0 is a decision, not a
#: blank, so fill-in must never touch them.
SOURCE_FLAG_FIELDS = ("disabled", "respect_robots")

#: Filled in only when the site has never set them. The kill switch is not here.
SETTING_DEFAULTS = {
	"competitor_user_agent": (
		"GoGizmoPriceBot/1.0 (+GoGizmo buyback price research; "
		"contact: pricing@gogizmo.example)"
	),
	"competitor_data_max_age_days": 7,
	"competitor_link_failure_limit": 5,
	"competitor_global_max_requests": 300,
}


def _load(name: str) -> list:
	with open(os.path.join(SEED_DIR, name), encoding="utf-8") as fh:
		return json.load(fh)


def _blank(value) -> bool:
	return value is None or (isinstance(value, str) and not value.strip())


def seed_sources(specs: list | None = None) -> dict:
	"""Create missing sources; fill blank fields on existing ones. Never overwrite."""
	specs = _load("sources.json") if specs is None else specs
	created, filled = [], []
	for spec in specs:
		name = spec["competitor_name"]
		if not frappe.db.exists("CH Competitor Source", name):
			doc = frappe.get_doc({"doctype": "CH Competitor Source", **spec})
			doc.insert(ignore_permissions=True)
			created.append(name)
			continue
		current = frappe.db.get_value(
			"CH Competitor Source", name, list(SOURCE_CONFIG_FIELDS), as_dict=True) or {}
		updates = {
			field: spec[field]
			for field in SOURCE_CONFIG_FIELDS
			if _blank(current.get(field)) and not _blank(spec.get(field))
		}
		if updates:
			frappe.db.set_value("CH Competitor Source", name, updates, update_modified=False)
			filled.append(name)
	return {"created": created, "filled": filled}


def seed_item_links(specs: list | None = None) -> dict:
	"""Create links for (competitor, item) pairs that have none. Skip the rest."""
	specs = _load("item_links.json") if specs is None else specs
	if not specs:
		return {"created": 0, "skipped": 0}

	existing = {
		(row.competitor, row.item_code)
		for row in frappe.get_all(
			"CH Competitor Item Link", fields=["competitor", "item_code"], limit_page_length=0)
	}
	sources = set(frappe.get_all("CH Competitor Source", pluck="name", limit_page_length=0))
	wanted = sorted({spec["item_code"] for spec in specs})
	items = set(frappe.get_all(
		"Item", filters={"name": ("in", wanted)}, pluck="name", limit_page_length=0))

	created = skipped = 0
	for spec in specs:
		key = (spec["competitor"], spec["item_code"])
		if key in existing or spec["competitor"] not in sources or spec["item_code"] not in items:
			skipped += 1
			continue
		frappe.get_doc({"doctype": "CH Competitor Item Link", **spec}).insert(ignore_permissions=True)
		existing.add(key)
		created += 1
	return {"created": created, "skipped": skipped}


def seed_settings() -> dict:
	"""Fill unset operational settings. Leaves competitor_collection_enabled alone."""
	meta = frappe.get_meta("CH Item Master Settings")
	filled = []
	for field, value in SETTING_DEFAULTS.items():
		if not meta.has_field(field):
			continue
		current = frappe.db.get_single_value("CH Item Master Settings", field)
		if _blank(current) or (isinstance(value, int) and cint(current) == 0):
			# set_single_value, not save(): the Single has unrelated mandatory
			# fields unset on some sites and a full save throws on them.
			frappe.db.set_single_value("CH Item Master Settings", field, value)
			filled.append(field)
	return {"filled": filled}


def after_migrate() -> dict:
	"""Migrate hook. Each step is isolated so one failure cannot skip the others."""
	summary = {}
	for label, step in (
		("sources", seed_sources),
		("settings", seed_settings),
		("item_links", seed_item_links),
	):
		try:
			summary[label] = step()
		except Exception:
			frappe.log_error(
				title=f"competitor seed: {label} failed", message=frappe.get_traceback())
			summary[label] = "failed"
	if not getattr(frappe.flags, "in_test", False):
		frappe.db.commit()
	frappe.logger("ch_item_master").info(f"competitor seed: {summary}")
	return summary


def live_regexes_compile() -> list[str]:
	"""Names of enabled seed sources whose price_regex fails to compile — a
	guard for the seed file itself, run by the tests."""
	broken = []
	for spec in _load("sources.json"):
		if cint(spec.get("disabled")) or not spec.get("price_regex"):
			continue
		try:
			re.compile(spec["price_regex"])
		except re.error:
			broken.append(spec["competitor_name"])
	return broken
