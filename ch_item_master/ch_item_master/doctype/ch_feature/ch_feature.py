# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from ch_item_master.id_sequences import next_numeric_id


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
		self.feature_id = next_numeric_id("feature")
