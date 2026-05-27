from frappe.model.document import Document


class VASProduct(Document):
	def validate(self):
		if not self.product_name and self.service_item:
			self.product_name = self.service_item
