# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHCategory(Document):
	def autoname(self):
		"""Auto-generate category_id before insert"""
		if not self.category_id:
			# Get next ID from sequence
			last_id = frappe.db.sql("""
				SELECT COALESCE(MAX(category_id), 0) 
				FROM `tabCH Category`
			""")[0][0]
			self.category_id = (last_id or 0) + 1
