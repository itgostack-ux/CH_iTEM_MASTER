# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from ch_item_master.ch_item_master.exceptions import (
	DuplicateManufacturerError,
	DuplicateSpecError,
	DuplicateSubCategoryError,
	InvalidHSNCodeError,
	InvalidNameOrderError,
	NamingOrderLockedError,
	SpecInUseError,
	SubCategoryInUseError,
	VariantFlagLockedError,
	VariantSpecRemovalError,
)


_TRANSACTION_TABLES = [
	"Sales Invoice Item",
	"Purchase Invoice Item",
	"Delivery Note Item",
	"Purchase Receipt Item",
	"Sales Order Item",
	"Purchase Order Item",
	"Stock Entry Detail",
]


class CHSubCategory(Document):
	def autoname(self):
		"""Auto-generate sub_category_id before insert"""
		if not self.sub_category_id:
			last_id = frappe.db.sql("""
				SELECT COALESCE(MAX(sub_category_id), 0) 
				FROM `tabCH Sub Category`
			""")[0][0]
			self.sub_category_id = (last_id or 0) + 1

	def validate(self):
		self.validate_unique_name_per_category()
		self.validate_case_insensitive_duplicate()
		self.validate_duplicate_manufacturers()
		self.validate_duplicate_specs()
		self.validate_name_order_sequential()
		self.validate_spec_changes_after_items_exist()
		self.validate_hsn_code()

	def validate_name_order_sequential(self):
		"""Ensure name_order values on specs with in_item_name=1 are sequential starting from 1.

		For example: 1, 2, 3 is valid. 1, 2, 5 is not.
		"""
		name_order_specs = []
		for row in self.specifications or []:
			if row.in_item_name and row.name_order:
				name_order_specs.append((row.idx, row.spec, int(row.name_order)))

		if not name_order_specs:
			return

		# Sort by name_order and check for gaps
		name_order_specs.sort(key=lambda x: x[2])
		expected = 1
		for idx, spec, order in name_order_specs:
			if order != expected:
				frappe.throw(
					_("Name Order must be sequential starting from 1. "
					  "Spec {0} (Row #{1}) has Name Order {2}, expected {3}. "
					  "Use consecutive numbers: 1, 2, 3, ..."
					).format(frappe.bold(spec), idx, order, expected),
					title=_("Invalid Name Order"),
					exc=InvalidNameOrderError,
				)
			expected += 1

		# Check for duplicate name_order values
		orders = [x[2] for x in name_order_specs]
		if len(orders) != len(set(orders)):
			frappe.throw(
				_("Duplicate Name Order values found. Each spec must have a unique sequential number."),
				title=_("Duplicate Name Order"),
				exc=InvalidNameOrderError,
			)

	def validate_hsn_code(self):
		"""Validate that the HSN code is 6 or 8 digits and exists in the GST HSN Code master.

		India Compliance rejects HSN codes with any other length at item save time,
		leading to a silent failure on the Item form.  Catching it here gives a clear,
		actionable error message at the sub-category level before items are created.
		"""
		if not self.hsn_code:
			return

		code = str(self.hsn_code).strip()

		# India Compliance valid lengths: 6 or 8 digits
		if len(code) not in (6, 8):
			frappe.throw(
				_(
					"HSN/SAC Code {0} must be 6 or 8 digits long "
					"(India Compliance requirement). Current length: {1} digits."
				).format(frappe.bold(code), len(code)),
				title=_("Invalid HSN Code"),
				exc=InvalidHSNCodeError,
			)

		# Verify it exists in the GST HSN Code master
		if not frappe.db.exists("GST HSN Code", code):
			frappe.throw(
				_(
					"HSN/SAC Code {0} does not exist in the GST HSN Code master. "
					"Please create it at <b>GST HSN Code &rarr; New</b> before "
					"using it in a Sub Category."
				).format(frappe.bold(code)),
				title=_("HSN Code Not Found"),
				exc=InvalidHSNCodeError,
			)

	def validate_unique_name_per_category(self):
		"""Ensure sub_category_name is unique within the same category.

		e.g. 'Screens' can exist under both 'Phone Spares' and 'Laptop Spares'
		but not twice under the same category.
		"""
		if not self.category or not self.sub_category_name:
			return

		existing = frappe.db.get_value(
			"CH Sub Category",
			{
				"category": self.category,
				"sub_category_name": self.sub_category_name,
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("Sub Category {0} already exists under Category {1}").format(
					frappe.bold(self.sub_category_name),
					frappe.bold(self.category),
				),
				title=_("Duplicate Sub Category"),
				exc=DuplicateSubCategoryError,
			)

	def validate_case_insensitive_duplicate(self):
		"""Case-insensitive duplicate check across ALL categories.

		The auto-name format is '{category}-{sub_category_name}' so two sub-categories
		under the same category with names differing only by case would collide.
		This gives a clear error instead of a DuplicateEntryError.
		"""
		if not self.category or not self.sub_category_name:
			return
		# Check via LOWER to catch collation mismatch
		dupes = frappe.db.sql("""
			SELECT name FROM `tabCH Sub Category`
			WHERE category = %s
			  AND LOWER(sub_category_name) = LOWER(%s)
			  AND name != %s
			LIMIT 1
		""", (self.category, self.sub_category_name, self.name or ""))
		if dupes:
			frappe.throw(
				_("Sub Category {0} already exists under Category {1} (as {2}). "
				  "Duplicate names are not allowed (case-insensitive check)."
				).format(
					frappe.bold(self.sub_category_name),
					frappe.bold(self.category),
					frappe.bold(dupes[0][0]),
				),
				title=_("Duplicate Sub Category"),
				exc=DuplicateSubCategoryError,
			)

	def validate_duplicate_manufacturers(self):
		"""Ensure no duplicate manufacturers in the child table."""
		seen = set()
		for row in self.manufacturers or []:
			if row.manufacturer in seen:
				frappe.throw(
					_("Row #{0}: Duplicate manufacturer {1}").format(
						row.idx, frappe.bold(row.manufacturer)
					),
					exc=DuplicateManufacturerError,
				)
			seen.add(row.manufacturer)

	def validate_duplicate_specs(self):
		"""Ensure no duplicate specs in the child table."""
		seen = set()
		for row in self.specifications or []:
			if row.spec in seen:
				frappe.throw(
					_("Row #{0}: Duplicate specification {1}").format(
						row.idx, frappe.bold(row.spec)
					),
					exc=DuplicateSpecError,
				)
			seen.add(row.spec)

	def validate_spec_changes_after_items_exist(self):
		"""Practical rules for spec changes depending on what data exists.

		Business reality: users WILL make mistakes. The rules are:

		ALWAYS ALLOWED (no items exist):
		  - Add/remove/reorder any spec freely

		AFTER MODELS EXIST (but no items):
		  - Add new specs (model just won't have values yet — fill them in)
		  - Change affects_price, in_item_name, name_order (safe — no items yet)
		  - Remove a spec only if no model has values for it
		  - Changing is_variant flag is blocked (would break model structure)

		AFTER ITEMS EXIST (but no submitted transactions):
		  - Add new NON-VARIANT specs (properties) freely
		  - Add new VARIANT specs with a warning (existing items won't have this variant)
		  - Change affects_price freely (just changes Ready Reckoner grouping)
		  - Change in_item_name / name_order with a warning (won't rename existing items)
		  - Block removing a variant spec (items reference it)
		  - Block changing is_variant (would break existing items)

		AFTER SUBMITTED TRANSACTIONS:
		  - Same as above, but name_order changes are fully blocked
		  - (To fix naming, user must do a data correction patch)
		"""
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before:
			return

		before_specs = {row.spec: row for row in (before.specifications or [])}
		current_specs = {row.spec: row for row in (self.specifications or [])}

		# Detect removed specs
		removed_specs = set(before_specs.keys()) - set(current_specs.keys())
		# Detect added specs
		added_specs = set(current_specs.keys()) - set(before_specs.keys())
		# Detect changed specs
		changed_variant_flag = []
		changed_naming = []
		for spec_name, row in current_specs.items():
			old = before_specs.get(spec_name)
			if not old:
				continue
			if old.is_variant != row.is_variant:
				changed_variant_flag.append(spec_name)
			if old.in_item_name != row.in_item_name or str(old.name_order or 0) != str(row.name_order or 0):
				changed_naming.append(spec_name)

		# If nothing changed, skip
		if not removed_specs and not added_specs and not changed_variant_flag and not changed_naming:
			return

		# Check what data exists
		models_count = frappe.db.count("CH Model", {"sub_category": self.name})
		items_count = frappe.db.count("Item", {"ch_sub_category": self.name}) if models_count else 0
		has_transactions = self._sub_category_used_in_transactions() if items_count else False

		# ── Block changing is_variant flag after models exist ──
		if changed_variant_flag and models_count:
			frappe.throw(
				_("Cannot change the Variant flag for {0} — {1} model(s) depend on this sub-category. "
				  "To fix: create a new spec with the correct setting, migrate data, then remove the old one."
				).format(
					", ".join(frappe.bold(s) for s in changed_variant_flag),
					models_count
				),
				title=_("Variant Flag Locked"),
				exc=VariantFlagLockedError,
			)

		# ── Block removing variant specs after items exist ──
		removed_variant_specs = [s for s in removed_specs if before_specs[s].is_variant]
		if removed_variant_specs and items_count:
			frappe.throw(
				_("Cannot remove variant spec(s) {0} — {1} item(s) use this sub-category. "
				  "Items already have variants based on these specs."
				).format(
					", ".join(frappe.bold(s) for s in removed_variant_specs),
					items_count
				),
				title=_("Cannot Remove Variant Spec"),
				exc=VariantSpecRemovalError,
			)

		# ── Block removing specs that models have values for ──
		if removed_specs and models_count:
			for spec_name in removed_specs:
				used_count = frappe.db.count(
					"CH Model Spec Value",
					{"parenttype": "CH Model", "spec": spec_name,
					 "parent": ("in", frappe.get_all("CH Model", {"sub_category": self.name}, pluck="name"))}
				)
				if used_count:
					frappe.throw(
						_("Cannot remove spec {0} — {1} model(s) have values for it. "
						  "Clear the spec values from models first."
						).format(frappe.bold(spec_name), used_count),
						title=_("Spec In Use"),
						exc=SpecInUseError,
					)

		# ── Block naming order changes after submitted transactions ──
		if changed_naming and has_transactions:
			frappe.throw(
				_("Cannot change naming configuration for {0} — items from this sub-category "
				  "have been used in submitted transactions. To fix item names, use a rename tool or data correction patch."
				).format(", ".join(frappe.bold(s) for s in changed_naming)),
				title=_("Naming Order Locked"),				exc=NamingOrderLockedError,			)

		# ── Warnings (non-blocking) ──
		warnings = []
		if changed_naming and items_count and not has_transactions:
			warnings.append(
				_("Naming order changed for {0}. Note: existing items will NOT be renamed. "
				  "Only new items will use the new naming order."
				).format(", ".join(frappe.bold(s) for s in changed_naming))
			)

		added_variant_specs = [s for s in added_specs if current_specs[s].is_variant]
		if added_variant_specs and items_count:
			warnings.append(
				_("New variant spec(s) {0} added. Existing items will not have this variant dimension. "
				  "You may need to create new variants for existing models."
				).format(", ".join(frappe.bold(s) for s in added_variant_specs))
			)

		if warnings:
			frappe.msgprint(
				"<br>".join(warnings),
				title=_("Spec Changes — Please Review"),
				indicator="orange",
			)

	def on_trash(self):
		"""Block deletion if models or items depend on this sub-category."""
		model_count = frappe.db.count("CH Model", {"sub_category": self.name})
		if model_count:
			frappe.throw(
				_("Cannot delete Sub Category {0} — {1} model(s) depend on it. "
				  "Delete or reassign the models first."
				).format(frappe.bold(self.sub_category_name), model_count),
				title=_("Sub Category In Use"),
				exc=SubCategoryInUseError,
			)

		item_count = frappe.db.count("Item", {"ch_sub_category": self.name})
		if item_count:
			frappe.throw(
				_("Cannot delete Sub Category {0} — {1} item(s) reference it."
				).format(frappe.bold(self.sub_category_name), item_count),
				title=_("Sub Category In Use"),
				exc=SubCategoryInUseError,
			)

	def _sub_category_used_in_transactions(self):
		"""Return True if any item belonging to this sub-category appears
		in at least one submitted transaction line."""
		items = frappe.get_all(
			"Item",
			filters={"ch_sub_category": self.name},
			pluck="name",
			limit=500,
		)
		if not items:
			return False

		for doctype in _TRANSACTION_TABLES:
			if frappe.db.exists(doctype, {"item_code": ("in", items), "docstatus": 1}):
				return True
		return False

