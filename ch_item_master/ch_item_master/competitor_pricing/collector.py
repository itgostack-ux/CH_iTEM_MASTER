# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Scheduled collection of competitor buyback prices.

The catalogue is ~7,000 mobile models and every one of them could be checked
against every competitor. Doing that in one run would be both useless and
rude, so the collector works as a **stalest-first rotation**: each run takes
the N links whose price we have not refreshed for longest and refreshes only
those. Coverage is therefore a function of how often the job runs and how high
the per-source limit is, and the full catalogue is reached over days rather
than hammered in minutes.

Discipline this module keeps, none of which is optional:

* a master kill switch in CH Item Master Settings, off by default
* robots.txt honoured per source, with the verdict cached for the run
* a minimum delay between two requests to the same competitor
* an honest, identifiable User-Agent
* per-link backoff so a dead URL is not retried forever
* every outcome written as a snapshot, failures included — a run that found
  nothing must be as visible as one that found a price
"""

import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime

from ch_item_master.config import get_int_setting, get_setting
from ch_item_master.ch_item_master.competitor_pricing.adapters import get_adapter

#: Used when neither the source nor settings specify one. Says who we are and
#: how to reach us — a site owner who wants us to stop should be able to ask.
DEFAULT_USER_AGENT = (
	"GoGizmoPriceBot/1.0 (+buyback price research; contact: pricing@gogizmo.example)"
)

_MAX_SLUG_WORDS = 12


def _slugify(text: str) -> str:
	cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in cstr(text))
	return "-".join(cleaned.split()[:_MAX_SLUG_WORDS])


class _RobotsCache:
	"""One robots.txt fetch per host per run, not per request."""

	def __init__(self, user_agent: str, timeout: int):
		self.user_agent = user_agent
		self.timeout = timeout
		self._verdicts = {}

	def allows(self, url: str) -> bool:
		import requests

		parts = urlparse(url)
		host = f"{parts.scheme}://{parts.netloc}"
		if host not in self._verdicts:
			parser = RobotFileParser()
			try:
				response = requests.get(
					urljoin(host, "/robots.txt"),
					timeout=self.timeout,
					headers={"User-Agent": self.user_agent},
				)
				# No robots.txt at all is permission by absence, which is the
				# standard reading. A 5xx is not — we back off instead.
				if response.status_code >= 500:
					self._verdicts[host] = None
					return False
				parser.parse(response.text.splitlines() if response.ok else [])
			except Exception:
				# Unreachable robots.txt is treated as "do not proceed". Being
				# wrong in the cautious direction costs us a day of data.
				self._verdicts[host] = None
				return False
			self._verdicts[host] = parser

		parser = self._verdicts[host]
		if parser is None:
			return False
		try:
			return parser.can_fetch(self.user_agent, url)
		except Exception:
			return False


def _resolve_url(source, link) -> str:
	"""An explicit link always wins; the template is the fallback."""
	if (link.get("url") or "").strip():
		return link["url"].strip()

	template = (source.get("url_template") or "").strip()
	if not template:
		return ""

	slug = _slugify(link.get("item_name") or link.get("item_code"))
	return template.replace("{slug}", slug)


def _due_links(source, limit: int) -> list:
	"""Stalest first. Never-checked links sort ahead of everything."""
	failure_limit = get_int_setting("competitor_link_failure_limit", 5, minimum=1)

	return frappe.get_all(
		"CH Competitor Item Link",
		filters={
			"competitor": source["name"],
			"match_status": ("!=", "Not Available"),
			"consecutive_failures": ("<", failure_limit),
		},
		fields=["name", "item_code", "item_name", "url", "match_status"],
		# MariaDB sorts NULL first on ASC, so links never checked come ahead of
		# every checked one — which is exactly the rotation we want. Expressed
		# as a plain field because Frappe's query engine rejects SQL functions
		# in order_by.
		order_by="last_checked_at asc",
		limit_page_length=limit,
	)


def _record_snapshot(source, link, url, observation, status, http_status=0, error=""):
	"""Every attempt becomes a row. Silent failures are how a price feed rots
	without anyone noticing."""
	snapshot = frappe.new_doc("CH Competitor Price Snapshot")
	snapshot.update({
		"competitor": source["name"],
		"item_code": link["item_code"],
		"item_name": link.get("item_name"),
		"condition_profile": source.get("default_condition_profile") or "Good",
		"captured_at": now_datetime(),
		"collection_method": "Automated Fetch",
		"confidence": source.get("confidence") or "Medium",
		"source_url": url,
		"fetch_status": status,
		"http_status": cint(http_status),
		"error_message": error[:500] if error else None,
	})

	if observation:
		snapshot.advertised_max = observation.advertised_max
		snapshot.evaluated_quote = observation.evaluated_quote
		snapshot.refurb_selling_price = observation.refurb_selling_price
		snapshot.raw_excerpt = observation.raw_excerpt
		if observation.notes and not error:
			snapshot.error_message = "; ".join(observation.notes)[:500]

	snapshot.flags.ignore_permissions = True
	snapshot.insert()
	return snapshot


def _update_link(link_name: str, status: str, failed: bool, remarks: str = ""):
	updates = {
		"last_checked_at": now_datetime(),
		"last_fetch_status": status,
		"remarks": remarks[:500] if remarks else None,
	}
	if failed:
		current = cint(frappe.db.get_value("CH Competitor Item Link", link_name, "consecutive_failures"))
		updates["consecutive_failures"] = current + 1
	else:
		updates["consecutive_failures"] = 0
		updates["match_status"] = "Auto Matched"

	frappe.db.set_value("CH Competitor Item Link", link_name, updates, update_modified=True)


def collect_for_source(source_name: str, limit: int = None) -> dict:
	"""Refresh the stalest links for one competitor. Returns a run summary."""
	import requests

	source = frappe.get_doc("CH Competitor Source", source_name).as_dict()
	summary = {"competitor": source_name, "fetched": 0, "priced": 0, "blocked": 0, "errors": 0, "skipped": 0}

	if source.get("disabled") or source.get("adapter") == "Manual Only":
		summary["skipped"] = 1
		return summary

	limit = min(cint(limit or source.get("max_requests_per_run") or 200), 5000)
	links = _due_links(source, limit)
	if not links:
		return summary

	user_agent = (
		(source.get("user_agent") or "").strip()
		or cstr(get_setting("competitor_user_agent", "")).strip()
		or DEFAULT_USER_AGENT
	)
	timeout = cint(source.get("timeout_seconds") or 15)
	delay = cint(source.get("request_delay_ms") or 2000) / 1000.0
	adapter = get_adapter(source.get("adapter"))
	robots = _RobotsCache(user_agent, timeout) if source.get("respect_robots") else None

	session = requests.Session()
	session.headers.update({"User-Agent": user_agent, "Accept-Language": "en-IN,en;q=0.9"})

	for index, link in enumerate(links):
		url = _resolve_url(source, link)
		if not url:
			_update_link(link["name"], "Error", True, "no URL and no usable template")
			summary["errors"] += 1
			continue

		if robots and not robots.allows(url):
			_record_snapshot(source, link, url, None, "Blocked", error="disallowed by robots.txt")
			_update_link(link["name"], "Blocked", True, "disallowed by robots.txt")
			summary["blocked"] += 1
			continue

		# Space out requests to the same host. The first request needs no wait.
		if index:
			time.sleep(delay)

		try:
			response = session.get(url, timeout=timeout)
		except Exception as exc:
			_record_snapshot(source, link, url, None, "Error", error=str(exc))
			_update_link(link["name"], "Error", True, str(exc))
			summary["errors"] += 1
			continue

		summary["fetched"] += 1

		if response.status_code == 404:
			_record_snapshot(source, link, url, None, "Not Found", http_status=404)
			_update_link(link["name"], "Not Found", True, "page returned 404")
			continue

		if not response.ok:
			_record_snapshot(
				source, link, url, None, "Error",
				http_status=response.status_code,
				error=f"HTTP {response.status_code}",
			)
			_update_link(link["name"], "Error", True, f"HTTP {response.status_code}")
			summary["errors"] += 1
			continue

		observation = adapter(source, url, response.text)
		status = "Success" if observation.has_price else "Not Found"
		_record_snapshot(source, link, url, observation, status, http_status=response.status_code)
		_update_link(link["name"], status, not observation.has_price,
		             "; ".join(observation.notes))

		if observation.has_price:
			summary["priced"] += 1

	frappe.db.set_value("CH Competitor Source", source_name, {
		"last_run_at": now_datetime(),
		"last_run_status": _run_status(summary),
		"last_run_summary": (
			f"{summary['fetched']} fetched · {summary['priced']} priced · "
			f"{summary['blocked']} blocked · {summary['errors']} errors"
		),
	}, update_modified=False)

	return summary


def _run_status(summary: dict) -> str:
	if summary.get("skipped"):
		return "Skipped"
	if summary["priced"] and not summary["errors"] and not summary["blocked"]:
		return "Success"
	if summary["priced"]:
		return "Partial"
	return "Failed"


def collect_competitor_prices():
	"""Scheduler entry point. Refuses to do anything unless switched on."""
	if not cint(get_setting("competitor_collection_enabled", 0)):
		return

	global_budget = get_int_setting("competitor_global_max_requests", 1000, minimum=1)
	sources = frappe.get_all(
		"CH Competitor Source",
		filters={"disabled": 0, "adapter": ("!=", "Manual Only")},
		pluck="name",
		# MariaDB already places NULL first for ascending order, which gives us
		# the intended never-run-first rotation. Keep this as a plain field:
		# Frappe v16 rejects SQL functions in validated order_by clauses.
		order_by="last_run_at asc, name asc",
	)

	summaries = []
	for source_name in sources:
		if global_budget <= 0:
			break
		try:
			summary = collect_for_source(source_name, limit=global_budget)
		except Exception:
			frappe.log_error(
				title=f"Competitor collection failed: {source_name}",
				message=frappe.get_traceback(),
			)
			frappe.db.set_value("CH Competitor Source", source_name, {
				"last_run_at": now_datetime(),
				"last_run_status": "Failed",
				"last_run_summary": "Run raised an exception — see Error Log.",
			}, update_modified=False)
			continue

		global_budget -= summary["fetched"]
		summaries.append(summary)
		frappe.db.commit()

	return summaries


@frappe.whitelist()
def run_now(competitor: str, limit: int = 25) -> dict:
	"""Operator-triggered trial run, so a new source can be proved before it
	is left to the scheduler. Deliberately capped low."""
	frappe.has_permission("CH Competitor Source", "write", competitor, throw=True)

	if not cint(get_setting("competitor_collection_enabled", 0)):
		frappe.throw(
			_("Automated competitor collection is switched off in CH Item Master Settings."),
			title=_("Collection Disabled"),
		)

	summary = collect_for_source(competitor, limit=min(cint(limit) or 25, 100))
	frappe.db.commit()
	return summary


@frappe.whitelist()
def build_item_links(competitor: str, item_group: str = "Mobiles", limit: int = 500) -> dict:
	"""Create missing links for a competitor from the item catalogue.

	Run repeatedly — each call takes the next tranche of unlinked items, so
	the full catalogue can be wired up without one enormous transaction.
	"""
	frappe.has_permission("CH Competitor Item Link", "create", throw=True)
	limit = min(cint(limit) or 500, 2000)

	existing = set(
		frappe.get_all("CH Competitor Item Link", filters={"competitor": competitor}, pluck="item_code")
	)
	candidates = frappe.get_all(
		"Item",
		filters={"disabled": 0, "item_group": item_group},
		fields=["name", "item_name"],
		order_by="name asc",
		limit_page_length=0,
	)

	created = 0
	for item in candidates:
		if created >= limit:
			break
		if item["name"] in existing:
			continue
		doc = frappe.new_doc("CH Competitor Item Link")
		doc.update({
			"competitor": competitor,
			"item_code": item["name"],
			"match_status": "Unmatched",
			"matched_by": "Slug",
		})
		doc.flags.ignore_permissions = True
		doc.insert()
		created += 1

	frappe.db.commit()
	remaining = max(len(candidates) - len(existing) - created, 0)
	return {"created": created, "remaining": remaining}
