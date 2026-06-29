import frappe
from frappe.model.document import Document


class CHMessageTemplate(Document):
	def validate(self):
		# One enabled template per (company, code, channel). A NULL company means
		# the global fallback; we still keep it unique within the NULL bucket.
		if not self.enabled:
			return
		filters = {
			"code": self.code,
			"channel": self.channel,
			"enabled": 1,
			"name": ["!=", self.name],
		}
		filters["company"] = self.company or ["is", "not set"]
		dupe = frappe.db.exists("CH Message Template", filters)
		if dupe:
			scope = self.company or frappe._("global fallback")
			frappe.throw(
				frappe._(
					"An enabled template for {0} / {1} already exists for {2}: {3}"
				).format(self.code, self.channel, scope, dupe)
			)
