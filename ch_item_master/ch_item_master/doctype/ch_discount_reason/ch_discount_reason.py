import frappe
from frappe.model.document import Document


class CHDiscountReason(Document):
	def validate(self):
		if not self.allow_manual_entry and not self.discount_value:
			frappe.throw(
				frappe._("Discount Value is required when manual entry is not allowed."),
				title=frappe._("Validation Error"),
			)
