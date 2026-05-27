import frappe
from frappe.model.document import Document


class VASPlan(Document):
	def validate(self):
		if self.source_warranty_plan and frappe.db.exists("CH Warranty Plan", self.source_warranty_plan):
			src = frappe.get_cached_doc("CH Warranty Plan", self.source_warranty_plan)
			self.plan_name = self.plan_name or src.plan_name
			self.duration_months = self.duration_months or src.duration_months
			self.list_price = self.list_price or src.price
			self.attach_level = self.attach_level or src.attach_level
			if not self.status:
				self.status = "Active" if (src.status or "") == "Active" else "Inactive"
