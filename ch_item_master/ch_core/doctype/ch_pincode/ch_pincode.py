import frappe
from frappe.model.document import Document


class CHPincode(Document):
	def validate(self):
		if self.pincode:
			self.pincode = self.pincode.strip()
			if not self.pincode.isdigit() or len(self.pincode) != 6:
				frappe.throw(frappe._("Pincode must be exactly 6 digits"))

		# Auto-fill state from the linked city if not set
		if self.city and not self.state:
			state = frappe.db.get_value("CH City", self.city, "state")
			if state:
				self.state = state
