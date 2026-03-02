# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHFeatureGroup(Document):
	def before_insert(self):
		self._assign_id()

	def before_save(self):
		if self.group_name:
			self.group_name = " ".join(self.group_name.split())

	def _assign_id(self):
		"""Auto-assign a unique integer feature_group_id for API / mobile use."""
		if self.feature_group_id:
			return
		result = frappe.db.sql(
			"SELECT COALESCE(MAX(feature_group_id), 0) FROM `tabCH Feature Group`"
		)
		self.feature_group_id = (result[0][0] or 0) + 1
