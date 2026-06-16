import frappe
from frappe.model.document import Document


class CHCity(Document):
	def validate(self):
		if self.city_name:
			self.city_name = self.city_name.strip().title()

		if self.state and self.city_name:
			existing = frappe.db.get_value(
				"CH City",
				{"state": self.state, "city_name": self.city_name, "name": ["!=", self.name]},
				"name",
			)
			if existing:
				frappe.throw(
					frappe._("City {0} already exists in state {1}.").format(
						frappe.bold(self.city_name), frappe.bold(self.state)
					),
					title=frappe._("Duplicate City"),
				)
