# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class CHItemCommercialTag(Document):
	def validate(self):
		self._validate_dates()
		self._auto_expire()
		self._warn_duplicate_active_tag()

	def _validate_dates(self):
		if self.effective_from and self.effective_to:
			if getdate(self.effective_to) < getdate(self.effective_from):
				frappe.throw(
					_("Effective To cannot be before Effective From"),
					title=_("Invalid Dates"),
				)

	def _auto_expire(self):
		if self.effective_to and getdate(self.effective_to) < getdate(nowdate()):
			self.status = "Expired"

	def _warn_duplicate_active_tag(self):
		"""Warn (not block) if the same item already has this tag active."""
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
				_("Item {0} already has an active <b>{1}</b> tag ({2}). "
				  "Consider expiring it first.").format(
					frappe.bold(self.item_code), self.tag, existing
				),
				indicator="orange",
				title=_("Duplicate Active Tag"),
			)
