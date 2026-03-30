# Copyright (c) 2025, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHCEODashboardSettings(Document):
	def validate(self):
		total = (
			(self.conversion_weight or 0) +
			(self.gocare_attach_weight or 0) +
			(self.revenue_vs_target_weight or 0) +
			(self.discount_control_weight or 0) +
			(self.repair_tat_weight or 0) +
			(self.session_discipline_weight or 0)
		)
		if total != 100:
			frappe.throw(f"Scorecard weights must total 100%. Currently: {total}%")
