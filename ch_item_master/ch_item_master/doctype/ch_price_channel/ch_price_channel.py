# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHPriceChannel(Document):
	def autoname(self):
		"""Auto-generate channel_id before insert"""
		if not self.channel_id:
			last_id = frappe.db.sql("""
				SELECT COALESCE(MAX(channel_id), 0) 
				FROM `tabCH Price Channel`
			""")[0][0]
			self.channel_id = (last_id or 0) + 1
