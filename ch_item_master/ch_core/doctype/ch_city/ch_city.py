import frappe
from frappe.model.document import Document


class CHCity(Document):
	def autoname(self):
		"""Use a state-aware key so duplicate district names across states do not collide.

		Examples:
		- Bilaspur-CG
		- Bilaspur-HP
		
		If state/state_code is missing, fall back to city-only.
		"""
		city = (self.city_name or "").strip().title()
		if not city:
			return

		state_token = None
		if self.state:
			state_code = (frappe.db.get_value("CH State", self.state, "state_code") or "").strip().upper()
			if state_code:
				state_token = state_code
			else:
				# Last-resort fallback if legacy states lack state_code.
				state_token = "".join(ch for ch in self.state.upper() if ch.isalnum())

		self.name = f"{city}-{state_token}" if state_token else city

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
