# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHPriceChannel(Document):
	def autoname(self):
		"""Auto-generate channel_id before insert"""
		if self.channel_name:
			self.channel_name = " ".join(self.channel_name.split())
		if not self.channel_id:
			last_id = frappe.db.sql("""
				SELECT COALESCE(MAX(channel_id), 0) 
				FROM `tabCH Price Channel`
			""")[0][0]
			self.channel_id = (last_id or 0) + 1

	def validate(self):
		if self.channel_name:
			self.channel_name = " ".join(self.channel_name.split())
		self._validate_price_list()
		self._validate_deactivation()

	def _validate_price_list(self):
		"""Warn if no Price List is linked — prices won't sync to ERPNext without it."""
		if not self.price_list:
			frappe.msgprint(
				_("No ERPNext Price List is linked to this channel. "
				  "Item prices set for this channel will NOT sync to ERPNext transactions. "
				  "Link a Price List if you want automatic price syncing."),
				indicator="orange",
				title=_("No Price List Linked"),
			)

	def _validate_deactivation(self):
		"""Warn when disabling a channel that has active prices or offers."""
		if self.is_new() or not self.disabled:
			return

		before = self.get_doc_before_save()
		if not before or before.disabled:
			return  # was already disabled

		active_prices = frappe.db.count(
			"CH Item Price",
			{"channel": self.name, "status": ("in", ["Active", "Scheduled"])},
		)
		active_offers = frappe.db.count(
			"CH Item Offer",
			{"channel": self.name, "status": ("in", ["Active", "Scheduled"])},
		)

		warnings = []
		if active_prices:
			warnings.append(_("{0} active/scheduled price record(s)").format(active_prices))
		if active_offers:
			warnings.append(_("{0} active/scheduled offer(s)").format(active_offers))

		if warnings:
			frappe.msgprint(
				_("This channel has {0}. "
				  "Deactivating it will NOT auto-expire these records — "
				  "they will remain active but invisible to new transactions."
				).format(" and ".join(warnings)),
				indicator="orange",
				title=_("Channel Has Active Records"),
			)

	def on_trash(self):
		"""Block deletion if prices or offers reference this channel."""
		price_count = frappe.db.count("CH Item Price", {"channel": self.name})
		offer_count = frappe.db.count("CH Item Offer", {"channel": self.name})
		total = price_count + offer_count
		if total:
			frappe.throw(
				_("Cannot delete Channel {0} — {1} price record(s) and {2} offer(s) reference it. "
				  "Deactivate it instead."
				).format(frappe.bold(self.channel_name), price_count, offer_count),
				title=_("Channel In Use"),
			)
