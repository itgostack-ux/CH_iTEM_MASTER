# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Adapters turn one competitor page into one price observation.

Each adapter is a callable ``(source, url, html_or_json) -> Observation``. The
fetching, throttling and robots handling all live in ``collector`` — an adapter
only parses, which keeps it trivially testable without touching the network.

Adding a competitor should not mean writing code. ``Generic HTML`` covers a
page that renders its price into the markup, ``Generic JSON`` covers a quote
endpoint, and both are driven entirely by selectors configured on the
CH Competitor Source record. Write a bespoke adapter only when a competitor
genuinely cannot be expressed that way.
"""

import json
import re
from dataclasses import dataclass, field

import frappe
from frappe import _

#: Digits, optional thousands separators, optional decimals. Deliberately does
#: not anchor on the rupee sign — competitors render it a dozen different ways.
_PRICE_PATTERN = re.compile(r"(\d[\d,\s]*(?:\.\d{1,2})?)")

#: Longest raw fragment kept on a snapshot for audit.
_EXCERPT_LIMIT = 2000


@dataclass
class Observation:
	"""What a single page yielded. Empty prices are normal, not an error —
	plenty of pages simply do not list the model we asked about."""

	advertised_max: float = 0.0
	evaluated_quote: float = 0.0
	refurb_selling_price: float = 0.0
	raw_excerpt: str = ""
	notes: list = field(default_factory=list)

	@property
	def has_price(self) -> bool:
		return bool(self.advertised_max or self.evaluated_quote)


def parse_price(text: str) -> float:
	"""Pull a number out of whatever the page calls a price.

	Handles '₹44,700', 'Up to Rs. 44700/-', '44 700.00' and the Indian
	grouping style '₹1,04,700' — the separators are stripped rather than
	interpreted, so grouping convention never matters.
	"""
	if not text:
		return 0.0

	match = _PRICE_PATTERN.search(str(text))
	if not match:
		return 0.0

	cleaned = match.group(1).replace(",", "").replace(" ", "").strip()
	try:
		value = float(cleaned)
	except ValueError:
		return 0.0

	# A "price" of 0 or a stray year like 2026 is noise, not a quote.
	return value if value >= 100 else 0.0


def _select_text(soup, selector: str) -> str:
	if not selector:
		return ""
	try:
		node = soup.select_one(selector)
	except Exception:
		# An invalid selector is a config error, not a fetch failure — say so
		# plainly rather than silently returning no price forever.
		frappe.throw(
			_("Invalid CSS selector: {0}").format(selector),
			title=_("Bad Selector"),
		)
	return node.get_text(" ", strip=True) if node else ""


def generic_html(source, url: str, payload: str) -> Observation:
	"""Parse a rendered product page using the source's configured selectors."""
	from bs4 import BeautifulSoup

	obs = Observation(raw_excerpt=(payload or "")[:_EXCERPT_LIMIT])
	soup = BeautifulSoup(payload or "", "lxml")

	advertised_text = _select_text(soup, source.get("price_selector"))
	quote_text = _select_text(soup, source.get("quote_selector"))

	obs.advertised_max = parse_price(advertised_text)
	obs.evaluated_quote = parse_price(quote_text)

	# Regex is the fallback, not the default: it runs against the selector's
	# text when we found the node, and against the whole page only when the
	# selector missed entirely.
	pattern = (source.get("price_regex") or "").strip()
	if pattern and not obs.advertised_max:
		try:
			found = re.search(pattern, advertised_text or payload or "")
		except re.error as exc:
			frappe.throw(
				_("Invalid Price Regex on {0}: {1}").format(source.get("name"), exc),
				title=_("Bad Regex"),
			)
		if found:
			obs.advertised_max = parse_price(found.group(found.re.groups and 1 or 0))

	if not obs.has_price:
		obs.notes.append("no price matched the configured selectors")

	return obs


def generic_json(source, url: str, payload: str) -> Observation:
	"""Parse a quote endpoint. Selectors are dotted paths into the response."""
	obs = Observation(raw_excerpt=(payload or "")[:_EXCERPT_LIMIT])

	try:
		data = json.loads(payload or "{}")
	except ValueError:
		obs.notes.append("response was not valid JSON")
		return obs

	def _dig(path: str):
		if not path:
			return None
		node = data
		for part in path.split("."):
			if isinstance(node, list):
				try:
					node = node[int(part)]
					continue
				except (ValueError, IndexError):
					return None
			if not isinstance(node, dict) or part not in node:
				return None
			node = node[part]
		return node

	obs.advertised_max = parse_price(_dig(source.get("price_selector")))
	obs.evaluated_quote = parse_price(_dig(source.get("quote_selector")))

	if not obs.has_price:
		obs.notes.append("no price at the configured JSON paths")

	return obs


def embedded_payload(source, url: str, payload: str) -> Observation:
	"""Read prices out of data embedded in the page rather than rendered text.

	Modern sell-your-phone sites are single-page apps: the visible price is
	painted by JavaScript, so there is no element to select. The numbers are
	still delivered in the initial response though — inside a hydration
	payload, an embedded JSON island or a structured-data block — which is
	where this adapter looks.

	Unlike ``generic_html`` it collects **every** match and takes the highest
	as the advertised maximum, because such a page usually carries one price
	per storage variant and the headline figure is the top of that set. The
	lowest is kept as the evaluated quote only when the source says so, since
	a low variant price is not the same thing as a condition-adjusted quote.
	"""
	obs = Observation(raw_excerpt=(payload or "")[:_EXCERPT_LIMIT])

	pattern = (source.get("price_regex") or "").strip()
	if not pattern:
		obs.notes.append("embedded adapter needs a Price Regex")
		return obs

	try:
		matches = re.findall(pattern, payload or "")
	except re.error as exc:
		frappe.throw(
			_("Invalid Price Regex on {0}: {1}").format(source.get("name"), exc),
			title=_("Bad Regex"),
		)

	values = []
	for match in matches:
		# findall yields a tuple when the pattern has several groups.
		candidate = match if isinstance(match, str) else next((g for g in match if g), "")
		price = parse_price(candidate)
		if price:
			values.append(price)

	if not values:
		obs.notes.append("regex matched no usable price in the embedded payload")
		return obs

	obs.advertised_max = max(values)
	obs.notes.append(f"{len(values)} variant price(s) found; range {min(values):.0f}-{max(values):.0f}")

	# The refurb selector, when set, is read with the same regex discipline.
	resale_pattern = (source.get("quote_selector") or "").strip()
	if resale_pattern:
		try:
			resale = [parse_price(m if isinstance(m, str) else next((g for g in m if g), ""))
			          for m in re.findall(resale_pattern, payload or "")]
		except re.error:
			resale = []
		resale = [r for r in resale if r]
		if resale:
			obs.refurb_selling_price = max(resale)

	return obs


def manual_only(source, url: str, payload: str) -> Observation:
	"""A source whose numbers are typed in by a human. Never fetched."""
	return Observation(notes=["manual source — collector does not fetch it"])


#: Adapter key on CH Competitor Source → parser. Extend here when a competitor
#: needs bespoke handling, and add the key to the doctype's Select options.
REGISTRY = {
	"Generic HTML": generic_html,
	"Generic JSON": generic_json,
	"Embedded Payload": embedded_payload,
	"Manual Only": manual_only,
}


def get_adapter(name: str):
	adapter = REGISTRY.get(name)
	if not adapter:
		frappe.throw(
			_("Unknown competitor adapter: {0}. Known adapters are {1}.").format(
				name, ", ".join(sorted(REGISTRY))
			),
			title=_("Unknown Adapter"),
		)
	return adapter
