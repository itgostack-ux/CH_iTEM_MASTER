# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHModel(Document):
	def autoname(self):
		"""Auto-generate model_id before insert with advisory lock for concurrency."""
		if not self.model_id:
			lock_name = "ch_model_autoname"
			frappe.db.sql("SELECT GET_LOCK(%s, 10)", lock_name)
			try:
				last_id = frappe.db.sql("""
					SELECT COALESCE(MAX(model_id), 0) 
					FROM `tabCH Model`
				""")[0][0]
				self.model_id = (last_id or 0) + 1
			finally:
				frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name)

	def validate(self):
		self.validate_unique_model_in_sub_category()
		self.validate_manufacturer_allowed()
		self.validate_brand_belongs_to_manufacturer()
		self.validate_spec_values()
		self.validate_variant_specs_have_values()
		self.validate_deactivation()

	def validate_unique_model_in_sub_category(self):
		"""Ensure model_name is unique within the same sub-category.

		The same model name (e.g. 'iPhone 15') shouldn't appear twice under
		'Smartphones', but can exist under different sub-categories if needed.
		"""
		if not self.sub_category or not self.model_name:
			return
		existing = frappe.db.get_value(
			"CH Model",
			{
				"sub_category": self.sub_category,
				"model_name": self.model_name,
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("Model {0} already exists under Sub Category {1} ({2})").format(
					frappe.bold(self.model_name),
					frappe.bold(self.sub_category),
					existing,
				),
				title=_("Duplicate Model"),
			)

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
				_("Manufacturer {0} is not allowed for Sub Category {1}. "
				  "Allowed: {2}. Add it to the sub-category's manufacturer list first."
				).format(
					frappe.bold(self.manufacturer),
					frappe.bold(self.sub_category),
					", ".join(frappe.bold(m) for m in allowed),
				),
				title=_("Manufacturer Not Allowed"),
			)

	def validate_brand_belongs_to_manufacturer(self):
		"""Ensure the brand belongs to the selected manufacturer (if ch_manufacturer custom field exists on Brand)."""
		if not self.brand or not self.manufacturer:
			return

		brand_manufacturer = frappe.db.get_value("Brand", self.brand, "ch_manufacturer")
		if brand_manufacturer and brand_manufacturer != self.manufacturer:
			frappe.throw(
				_("Brand {0} belongs to Manufacturer {1}, not {2}. "
				  "Either change the brand or change the manufacturer."
				).format(
					frappe.bold(self.brand),
					frappe.bold(brand_manufacturer),
					frappe.bold(self.manufacturer),
				),
				title=_("Brand-Manufacturer Mismatch"),
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

		seen_pairs = set()
		for row in self.spec_values or []:
			# Validate spec belongs to sub-category
			if allowed_specs and row.spec not in allowed_specs:
				frappe.throw(
					_("Row #{0}: Specification {1} is not defined for Sub Category {2}. "
					  "Available specs: {3}"
					).format(
						row.idx,
						frappe.bold(row.spec),
						frappe.bold(self.sub_category),
						", ".join(allowed_specs),
					),
					title=_("Invalid Specification"),
				)

			# Check for duplicate spec+value pairs
			pair = (row.spec, row.spec_value)
			if pair in seen_pairs:
				frappe.throw(
					_("Row #{0}: Duplicate entry — {1}: {2} is already added above.").format(
						row.idx, frappe.bold(row.spec), frappe.bold(row.spec_value)
					),
					title=_("Duplicate Spec Value"),
				)
			seen_pairs.add(pair)

			# Validate spec_value actually exists in Item Attribute Value for this attribute
			if row.spec and row.spec_value:
				exists = frappe.db.exists(
					"Item Attribute Value",
					{"parent": row.spec, "attribute_value": row.spec_value},
				)
				if not exists:
					frappe.throw(
						_("Row #{0}: Value {1} does not exist in attribute {2}. "
						  "Go to Item Attribute → {2} to add this value first."
						).format(
							row.idx, frappe.bold(row.spec_value), frappe.bold(row.spec)
						),
						title=_("Invalid Attribute Value"),
					)

	def validate_variant_specs_have_values(self):
		"""Every spec defined in the sub-category must have at least one value in this model.

		Variant specs without values will block item generation.
		Property specs without values mean items will have incomplete data.
		"""
		if not self.sub_category:
			return

		all_specs = frappe.get_all(
			"CH Sub Category Spec",
			filters={
				"parent": self.sub_category,
				"parenttype": "CH Sub Category",
			},
			fields=["spec", "is_variant"],
		)
		if not all_specs:
			return

		model_specs = {row.spec for row in self.spec_values or []}

		missing_variant = [s.spec for s in all_specs if s.is_variant and s.spec not in model_specs]
		missing_property = [s.spec for s in all_specs if not s.is_variant and s.spec not in model_specs]

		if missing_variant:
			frappe.throw(
				_("Every variant spec must have at least one value. "
				  "Missing values for: {0}. "
				  "Add at least one value for each spec in the Spec Values table."
				).format(", ".join(frappe.bold(s) for s in missing_variant)),
				title=_("Missing Variant Spec Values"),
			)

		if missing_property:
			frappe.throw(
				_("Every property spec must have at least one value. "
				  "Missing values for: {0}. "
				  "Add at least one value for each spec in the Spec Values table."
				).format(", ".join(frappe.bold(s) for s in missing_property)),
				title=_("Missing Property Spec Values"),
			)

	def validate_deactivation(self):
		"""Block deactivation if items exist for this model."""
		if self.is_new() or self.is_active:
			return

		before = self.get_doc_before_save()
		if not before or not before.is_active:
			return  # was already inactive

		item_count = frappe.db.count("Item", {"ch_model": self.name})
		if item_count:
			frappe.msgprint(
				_("This model has {0} item(s) in the system. "
				  "Deactivating it will prevent new items from being created, "
				  "but existing items will remain unchanged."
				).format(frappe.bold(str(item_count))),
				indicator="orange",
				title=_("Items Exist"),
			)

	def on_trash(self):
		"""Block deletion if items reference this model."""
		item_count = frappe.db.count("Item", {"ch_model": self.name})
		if item_count:
			frappe.throw(
				_("Cannot delete Model {0} — {1} item(s) reference it. "
				  "Deactivate it instead."
				).format(frappe.bold(self.model_name), item_count),
				title=_("Model In Use"),
			)
