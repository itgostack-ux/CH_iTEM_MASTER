# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class CHItemCommercialTag(Document):
	def validate(self):
		self._validate_dates()
		self._auto_set_status()
		self._warn_duplicate_active_tag()
		self._validate_conflicting_tags()

	def _validate_dates(self):
		if self.effective_from and self.effective_to:
			if getdate(self.effective_to) < getdate(self.effective_from):
				frappe.throw(
					_("Effective To ({0}) cannot be before Effective From ({1})").format(
						self.effective_to, self.effective_from
					),
					title=_("Invalid Dates"),
				)

	def _auto_set_status(self):
		"""Auto-compute status based on effective dates.

		- Past end date → Expired
		- Before start date → Active (will become visible on start date via scheduled job)
		- Otherwise → Active
		"""
		today = getdate(nowdate())

		if self.effective_to and getdate(self.effective_to) < today:
			self.status = "Expired"
		elif not self.status or self.status == "Expired":
			# If it was Expired but dates were extended, or new record, set Active
			self.status = "Active"

	def _warn_duplicate_active_tag(self):
		"""Warn (not block) if the same item already has this tag active."""
		if self.status != "Active":
			return

		existing = frappe.db.get_value(
			"CH Item Commercial Tag",
			{
				"item_code": self.item_code,
				"tag": self.tag,
				"status": "Active",
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.msgprint(
				_("Item {0} already has an active {1} tag ({2}). "
				  "Consider expiring it first to avoid confusion."
				).format(
					frappe.bold(self.item_code), frappe.bold(self.tag), existing
				),
				indicator="orange",
				title=_("Duplicate Active Tag"),
			)

	def _validate_conflicting_tags(self):
		"""Block contradictory tags on the same item.

		e.g. 'EOL' and 'NEW' can't both be active on the same item —
		a product can't be end-of-life and new at the same time.
		"""
		if self.status != "Active":
			return

		conflicts = {
			"EOL": ["NEW", "PROMO FOCUS"],
			"NEW": ["EOL"],
			"RESTRICTED": ["PROMO FOCUS"],
		}
		conflicting = conflicts.get(self.tag, [])
		if not conflicting:
			return

		existing = frappe.db.get_value(
			"CH Item Commercial Tag",
			{
				"item_code": self.item_code,
				"tag": ("in", conflicting),
				"status": "Active",
				"name": ("!=", self.name),
			},
			["name", "tag"],
			as_dict=True,
		)
		if existing:
			frappe.throw(
				_("Cannot mark item as {0} — it already has an active {1} tag ({2}). "
				  "These tags are contradictory. Expire the conflicting tag first."
				).format(
					frappe.bold(self.tag),
					frappe.bold(existing.tag),
					existing.name,
				),
				title=_("Conflicting Tags"),
			)
