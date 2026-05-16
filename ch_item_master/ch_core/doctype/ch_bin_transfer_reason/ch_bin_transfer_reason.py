import frappe
from frappe.model.document import Document


class CHBinTransferReason(Document):
	def validate(self):
		if self.source_bin_type and self.source_bin_type == self.target_bin_type:
			frappe.throw(
				frappe._("Source bin and target bin cannot be the same."),
				title=frappe._("Invalid Reason"),
			)
