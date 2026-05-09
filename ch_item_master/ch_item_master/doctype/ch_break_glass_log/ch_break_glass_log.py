# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHBreakGlassLog(Document):

	def before_save(self):
		"""Auto-compute duration when end_time is set."""
		if self.start_time and self.end_time:
			from frappe.utils import time_diff_in_hours
			try:
				self.duration_hours = round(
					time_diff_in_hours(self.end_time, self.start_time), 2
				)
			except Exception:
				pass
