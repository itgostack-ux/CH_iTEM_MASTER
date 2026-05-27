import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt


class VASAttachRule(Document):
	def validate(self):
		self._validate_trigger()
		self._validate_band()

	def _validate_trigger(self):
		if not any([self.item_code, self.category, self.sub_category, self.brand]):
			frappe.throw(_("Select at least one trigger: Item, Category, Sub Category, or Brand"))

	def _validate_band(self):
		if self.min_price and self.max_price and flt(self.min_price) > flt(self.max_price):
			frappe.throw(_("Min Device Price cannot be greater than Max Device Price"))
