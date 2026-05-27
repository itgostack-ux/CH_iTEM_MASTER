import frappe
from frappe.model.document import Document


class VASPartner(Document):
	def validate(self):
		if self.partner_name and not self.title:
			self.title = self.partner_name
