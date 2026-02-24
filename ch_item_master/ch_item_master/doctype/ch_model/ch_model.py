# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHModel(Document):
	def validate(self):
		self.validate_manufacturer_allowed()
		self.validate_brand_belongs_to_manufacturer()
		self.validate_spec_values()

	def validate_manufacturer_allowed(self):
		"""Ensure the manufacturer is in the sub-category's allowed list."""
		if not self.sub_category or not self.manufacturer:
			return

		allowed = frappe.get_all(
			"CH Sub Category Manufacturer",
			filters={"parent": self.sub_category, "parenttype": "CH Sub Category"},
			pluck="manufacturer",
		)

		if allowed and self.manufacturer not in allowed:
			frappe.throw(
				_("Manufacturer {0} is not allowed for Sub Category {1}").format(
					frappe.bold(self.manufacturer), frappe.bold(self.sub_category)
				)
			)

	def validate_brand_belongs_to_manufacturer(self):
		"""Ensure the brand belongs to the selected manufacturer (if ch_manufacturer custom field exists on Brand)."""
		if not self.brand or not self.manufacturer:
			return

		brand_manufacturer = frappe.db.get_value("Brand", self.brand, "ch_manufacturer")
		if brand_manufacturer and brand_manufacturer != self.manufacturer:
			frappe.throw(
				_("Brand {0} does not belong to Manufacturer {1}").format(
					frappe.bold(self.brand), frappe.bold(self.manufacturer)
				)
			)

	def validate_spec_values(self):
		"""Ensure spec values belong to specs defined in the sub-category
		AND the value actually exists in Item Attribute Value for that attribute.

		Multiple values per spec ARE allowed (e.g. Color: Black + Color: White + Color: Blue),
		so the user can choose when creating an item variant.
		"""
		if not self.sub_category:
			return

		allowed_specs = frappe.get_all(
			"CH Sub Category Spec",
			filters={"parent": self.sub_category, "parenttype": "CH Sub Category"},
			pluck="spec",
		)

		for row in self.spec_values or []:
			# Validate spec belongs to sub-category
			if allowed_specs and row.spec not in allowed_specs:
				frappe.throw(
					_("Row #{0}: Specification {1} is not defined for Sub Category {2}").format(
						row.idx, frappe.bold(row.spec), frappe.bold(self.sub_category)
					)
				)

			# Validate spec_value actually exists in Item Attribute Value for this attribute
			if row.spec and row.spec_value:
				exists = frappe.db.exists(
					"Item Attribute Value",
					{"parent": row.spec, "attribute_value": row.spec_value},
				)
				if not exists:
					frappe.throw(
						_("Row #{0}: Value <b>{1}</b> does not exist in attribute <b>{2}</b>. "
						  "Please select a valid value.").format(
							row.idx, row.spec_value, row.spec
						)
					)
