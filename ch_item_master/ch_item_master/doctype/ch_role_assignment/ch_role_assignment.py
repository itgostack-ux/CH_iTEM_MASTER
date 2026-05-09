# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, today


class CHRoleAssignment(Document):

	def validate(self):
		if getdate(self.valid_from) > getdate(self.valid_to):
			frappe.throw(
				frappe._("Valid From must be on or before Valid To."),
				title=frappe._("Invalid Date Range"),
			)

