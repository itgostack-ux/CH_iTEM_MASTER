# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from ch_item_master.id_sequences import next_numeric_id


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
		self.feature_group_id = next_numeric_id("feature_group")
