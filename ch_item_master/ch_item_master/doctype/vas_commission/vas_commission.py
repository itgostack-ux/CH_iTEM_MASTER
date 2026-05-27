import frappe
from frappe.model.document import Document
from frappe.utils import flt


class VASCommission(Document):
	def validate(self):
		self.commission_base_amount = flt(self.commission_base_amount)
		self.commission_rate = flt(self.commission_rate)
		if not self.commission_amount:
			self.commission_amount = flt(self.commission_base_amount * self.commission_rate / 100)
