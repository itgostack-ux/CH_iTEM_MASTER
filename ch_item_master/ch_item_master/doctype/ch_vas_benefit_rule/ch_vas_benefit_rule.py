import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CHVASBenefitRule(Document):
	def validate(self):
		if flt(self.unit_limit) < 0:
			frappe.throw(_("Unit Limit cannot be negative"))
		if flt(self.value_limit) < 0:
			frappe.throw(_("Value Limit cannot be negative"))
		if flt(self.coverage_percent) < 0 or flt(self.coverage_percent) > 100:
			frappe.throw(_("Coverage Percent must be between 0 and 100"))
		if flt(self.deductible_amount) < 0:
			frappe.throw(_("Deductible Amount cannot be negative"))
