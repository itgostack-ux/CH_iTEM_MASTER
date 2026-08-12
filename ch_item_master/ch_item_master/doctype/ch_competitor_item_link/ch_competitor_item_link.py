# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHCompetitorItemLink(Document):
	def validate(self):
		self.url = (self.url or "").strip()
		self._validate_unique_pair()

	def _validate_unique_pair(self):
		"""One link per competitor × item. Duplicates would double-count a
		competitor in the median and quietly skew the whole band."""
		existing = frappe.db.exists(
			"CH Competitor Item Link",
			{
				"competitor": self.competitor,
				"item_code": self.item_code,
				"name": ("!=", self.name),
			},
		)
		if existing:
			frappe.throw(
				_("{0} is already linked to {1} ({2}).").format(
					frappe.bold(self.item_code), frappe.bold(self.competitor), existing
				),
				title=_("Duplicate Link"),
			)
