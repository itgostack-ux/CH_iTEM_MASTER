import frappe
from frappe.model.document import Document


class CHCity(Document):
	def validate(self):
		if self.city_name:
			self.city_name = self.city_name.strip().title()

		if self.state:
			self.state = self.state.strip().title()

		if self.company and self.city_name:
			existing = frappe.db.get_value(
				"CH City",
				{"company": self.company, "city_name": self.city_name, "name": ["!=", self.name]},
				"name",
			)
			if existing:
				frappe.throw(
					frappe._("City {0} already exists for company {1}.").format(
						frappe.bold(self.city_name), frappe.bold(self.company)
					),
					title=frappe._("Duplicate City"),
				)
