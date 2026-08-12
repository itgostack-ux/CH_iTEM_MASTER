# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Match our catalogue to competitor product URLs, using their own sitemaps.

A URL template plus a slugified item name almost never produces a working
competitor URL — every site names models differently ("used-apple-iphone-15-
plus-6-gb-256-gb" against "apple/apple-iphone-15" against our own "Apple
iPhone 15 Plus 256GB Black"). Guessing produces a catalogue of 404s.

So we do not guess. Each competitor publishes a sitemap listing every model
page it has; we read that, reduce both sides to a comparable key, and link
what genuinely corresponds. A sitemap is the discovery mechanism sites
publish *for* this purpose, which makes it both the most reliable and the
most polite route — one request yields thousands of URLs instead of
thousands of speculative requests yielding mostly nothing.

Matching is deliberately conservative: an unmatched item is visible work in
the planner, whereas a wrongly matched item silently poisons a price band.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint

#: Words that carry no model identity. Colours are excluded too — competitors
#: price a model, not a colourway, so keeping them would prevent every match.
_NOISE = frozenset("""
	used sell sold old new mobile mobiles phone phones smartphone smartphones
	sell-old buy price cash exchange gb tb ram rom storage variant dual sim
	5g 4g lte india online refurbished renewed
	black white blue green red gold silver grey gray graphite midnight
	starlight purple pink yellow orange titanium natural desert ultramarine
	teal lavender mint cream sierra pacific alpine space jet coral
""".split())

# Single digits count: a "6 GB" RAM figure left in the identity tokens is
# enough on its own to stop "iPhone 15 6 GB 128 GB" matching our "iPhone 15".
_STORAGE_RE = re.compile(r"(\d{1,4})\s*(gb|tb)\b", re.I)


def _parse(text: str) -> tuple:
	"""Split a device name into (identity tokens, storage GB).

	Capacities are pulled out **before** tokenising and compared separately.
	Leaving them in the token set makes containment fail for the wrong
	reason: our "iPhone 15 Plus 256GB" yields a `256gb` token while Cashify's
	"...-6-gb-256-gb" yields `6` and `256`, so two names for the same phone
	never line up. Extracting them first lets identity match on identity and
	capacity match on capacity.

	Single-character tokens are kept deliberately. A model number is often one
	digit — "Realme 9", "Pixel 8" — and dropping it reduces the competitor's
	page to its brand alone, which is a subset of every phone that brand
	makes. That is exactly how a first cut of this matched every Realme in the
	catalogue to one Realme 9 page.
	"""
	lowered = (text or "").lower()

	sizes = [
		int(n) * (1024 if unit.lower() == "tb" else 1)
		for n, unit in _STORAGE_RE.findall(lowered)
	]
	# Storage is the largest capacity quoted; anything smaller is RAM.
	storage = max(sizes) if sizes else None

	stripped = _STORAGE_RE.sub(" ", lowered)
	words = re.split(r"[^a-z0-9]+", stripped)
	tokens = frozenset(w for w in words if w and w not in _NOISE)
	return tokens, storage


#: A competitor page naming fewer than this many identity tokens is too vague
#: to link safely — a brand-only page would otherwise match its whole range.
_MIN_IDENTITY_TOKENS = 2


def _index_competitor_urls(urls: list, url_pattern: str) -> list:
	"""Reduce each competitor URL to (tokens, storage, url)."""
	pattern = re.compile(url_pattern) if url_pattern else None
	entries = []
	for url in urls:
		if pattern and not pattern.search(url):
			continue
		# The identifying part is the tail of the path, not the whole site.
		tail = url.rstrip("/").rsplit("/", 2)[-2:]
		tokens, storage = _parse(" ".join(tail).replace("-", " "))
		if len(tokens) < _MIN_IDENTITY_TOKENS:
			continue
		entries.append({"url": url, "tokens": tokens, "storage": storage})
	return entries


def _best_match(item_tokens: frozenset, item_storage: int | None, entries: list) -> dict | None:
	"""The competitor entry naming exactly this model, or nothing.

	Identity must match **exactly**, not by containment. Containment reads as
	the safer choice until you notice that a *less* specific page is a subset
	of a *more* specific phone: "iphone 15 pro" sits inside our "iPhone 15 Pro
	Max", so every Pro Max in the catalogue would be priced off the Pro's
	page. Requiring the identity sets to be equal costs some match rate and
	removes that whole class of error.

	Capacity is compared separately, and a page naming our capacity beats one
	naming none. Two equally good candidates return nothing: an unmatched item
	is visible work in the planner, whereas a wrongly matched one silently
	corrupts a price band.
	"""
	exact = [e for e in entries if e["tokens"] == item_tokens]
	if not exact:
		return None

	if item_storage:
		sized = [e for e in exact if e["storage"] == item_storage]
		if sized:
			return sized[0] if len({e["url"] for e in sized}) == 1 else None
		# Fall back to a model-level page, but never to one advertising a
		# capacity that is not the phone in front of us.
		exact = [e for e in exact if not e["storage"]]

	if not exact:
		return None
	return exact[0] if len({e["url"] for e in exact}) == 1 else None


def fetch_sitemap_urls(sitemap_url: str, user_agent: str, depth: int = 0) -> list:
	"""Collect <loc> entries, following one level of sitemap index nesting."""
	import requests

	if depth > 2:
		return []

	response = requests.get(sitemap_url, timeout=30, headers={"User-Agent": user_agent})
	response.raise_for_status()
	locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", response.text)

	# A sitemap index lists further sitemaps rather than pages.
	nested = [loc for loc in locs if loc.lower().endswith(".xml")]
	if nested and len(nested) == len(locs):
		collected = []
		for child in nested:
			try:
				collected.extend(fetch_sitemap_urls(child, user_agent, depth + 1))
			except Exception:
				continue
		return collected

	return [loc for loc in locs if not loc.lower().endswith(".xml")]


@frappe.whitelist()
def match_from_sitemap(
	competitor: str,
	sitemap_url: str,
	url_pattern: str = "",
	item_group: str = "Mobiles",
	limit: int = 2000,
	dry_run: int = 1,
) -> dict:
	"""Link our items to a competitor's model pages via their sitemap.

	Defaults to a dry run so the match quality can be inspected before
	thousands of link rows are written.
	"""
	frappe.has_permission("CH Competitor Item Link", "create", throw=True)
	source = frappe.get_doc("CH Competitor Source", competitor)

	from ch_item_master.ch_item_master.competitor_pricing.collector import DEFAULT_USER_AGENT

	user_agent = (source.user_agent or "").strip() or DEFAULT_USER_AGENT
	urls = fetch_sitemap_urls(sitemap_url, user_agent)
	entries = _index_competitor_urls(urls, url_pattern)

	items = frappe.get_all(
		"Item",
		filters={"disabled": 0, "item_group": item_group},
		fields=["name", "item_name"],
		limit_page_length=0,
	)

	existing = {
		row["item_code"]: row["name"]
		for row in frappe.get_all(
			"CH Competitor Item Link",
			filters={"competitor": competitor},
			fields=["name", "item_code"],
		)
	}

	limit = cint(limit) or 2000
	matched, created, updated, samples = 0, 0, 0, []

	for item in items:
		if matched >= limit:
			break

		label = item["item_name"] or item["name"]
		item_tokens, item_storage = _parse(label)
		best = _best_match(item_tokens, item_storage, entries)
		if not best:
			continue

		matched += 1
		if len(samples) < 12:
			samples.append({"item": label, "url": best["url"]})

		if cint(dry_run):
			continue

		if item["name"] in existing:
			frappe.db.set_value("CH Competitor Item Link", existing[item["name"]], {
				"url": best["url"],
				"match_status": "Auto Matched",
				"matched_by": "Search",
				"consecutive_failures": 0,
			}, update_modified=True)
			updated += 1
		else:
			doc = frappe.new_doc("CH Competitor Item Link")
			doc.update({
				"competitor": competitor,
				"item_code": item["name"],
				"url": best["url"],
				"match_status": "Auto Matched",
				"matched_by": "Search",
			})
			doc.flags.ignore_permissions = True
			doc.insert()
			created += 1

		if (created + updated) % 200 == 0:
			frappe.db.commit()

	if not cint(dry_run):
		frappe.db.commit()

	return {
		"sitemap_urls": len(urls),
		"model_pages": len(entries),
		"catalogue_items": len(items),
		"matched": matched,
		"created": created,
		"updated": updated,
		"dry_run": bool(cint(dry_run)),
		"samples": samples,
	}
