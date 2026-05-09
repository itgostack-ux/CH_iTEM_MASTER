# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHApprovalDelegation(Document):

	def validate(self):
		if self.delegator == self.delegate:
			frappe.throw(
				frappe._("Delegator and Delegate cannot be the same user."),
				title=frappe._("Invalid Delegation"),
			)
		if self.valid_from and self.valid_to:
			if frappe.utils.getdate(self.valid_from) > frappe.utils.getdate(self.valid_to):
				frappe.throw(
					frappe._("Valid From must be before Valid To."),
					title=frappe._("Invalid Date Range"),
				)
