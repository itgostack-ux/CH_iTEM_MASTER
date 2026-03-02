# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHFeature(Document):
	def before_insert(self):
		self._assign_id()

	def before_save(self):
		if self.feature_name:
			self.feature_name = " ".join(self.feature_name.split())

	def _assign_id(self):
		"""Auto-assign a unique integer feature_id for API / mobile use."""
		if self.feature_id:
			return
		result = frappe.db.sql(
			"SELECT COALESCE(MAX(feature_id), 0) FROM `tabCH Feature`"
		)
		self.feature_id = (result[0][0] or 0) + 1
