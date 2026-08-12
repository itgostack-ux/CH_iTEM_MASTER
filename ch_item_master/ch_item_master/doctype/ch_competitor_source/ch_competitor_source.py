# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class CHCompetitorSource(Document):
	def validate(self):
		self.competitor_name = " ".join((self.competitor_name or "").split())
		self.base_url = (self.base_url or "").strip().rstrip("/")

		if self.base_url and not self.base_url.startswith(("http://", "https://")):
			frappe.throw(
				_("Base URL must start with http:// or https://"),
				title=_("Invalid Base URL"),
			)

		self._validate_fetch_config()
		self._clamp_limits()

	def _validate_fetch_config(self):
		"""A fetched source needs somewhere to go and something to read."""
		if self.adapter == "Manual Only":
			return

		if not (self.url_template or "").strip():
			frappe.msgprint(
				_("No URL Template set. Only items with an explicit CH Competitor Item Link "
				  "will be fetched from {0}.").format(frappe.bold(self.competitor_name)),
				indicator="orange",
				title=_("No URL Template"),
			)

		if not (self.price_selector or "").strip() and not (self.price_regex or "").strip():
			frappe.throw(
				_("Set an Advertised Price Selector or a Price Regex — "
				  "the collector has no way to read a price otherwise."),
				title=_("No Extraction Rule"),
			)

	def _clamp_limits(self):
		"""Politeness floors. These protect the competitor's site and our IP."""
		self.request_delay_ms = max(cint(self.request_delay_ms) or 2000, 500)
		self.max_requests_per_run = min(max(cint(self.max_requests_per_run) or 200, 1), 5000)
		self.timeout_seconds = min(max(cint(self.timeout_seconds) or 15, 3), 60)

	def on_trash(self):
		snapshots = frappe.db.count("CH Competitor Price Snapshot", {"competitor": self.name})
		if snapshots:
			frappe.throw(
				_("Cannot delete {0} — {1} price snapshot(s) reference it. Disable it instead.")
				.format(frappe.bold(self.competitor_name), snapshots),
				title=_("Competitor In Use"),
			)
