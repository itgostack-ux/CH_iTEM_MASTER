# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


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
	def validate(self):
		self.validate_unique_name_per_category()
		self.validate_duplicate_manufacturers()
		self.validate_duplicate_specs()
		self.validate_name_order_not_changed_after_transactions()
		self.validate_hsn_code()

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
			)

	def validate_duplicate_manufacturers(self):
		"""Ensure no duplicate manufacturers in the child table."""
		seen = set()
		for row in self.manufacturers or []:
			if row.manufacturer in seen:
				frappe.throw(
					_("Row #{0}: Duplicate manufacturer {1}").format(
						row.idx, frappe.bold(row.manufacturer)
					)
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
					)
				)
			seen.add(row.spec)

	def validate_name_order_not_changed_after_transactions(self):
		"""Block changes to is_variant / in_item_name / name_order on spec rows
		once any item from this sub-category has been used in a transaction.

		These fields drive the generated item name; changing them after items
		exist in ledgers would break reporting and reconciliation.
		"""
		if self.is_new():
			return

		# Compare only variant specs (the ones that affect item naming)
		before = self.get_doc_before_save()
		if not before:
			return

		before_map = {
			row.spec: row
			for row in (before.specifications or [])
			if row.is_variant
		}

		changed_specs = []
		for row in self.specifications or []:
			if not row.is_variant:
				continue
			old = before_map.get(row.spec)
			if old and (
				old.in_item_name != row.in_item_name
				or str(old.name_order or "") != str(row.name_order or "")
			):
				changed_specs.append(frappe.bold(row.spec))

		if not changed_specs:
			return

		# Lazy-check: only hit the DB if something actually changed
		if not self._sub_category_used_in_transactions():
			return

		frappe.throw(
			_(
				"Cannot change naming order for {0} — items from this sub-category "
				"have already been used in transactions. Changing the naming order "
				"would break existing records and reporting."
			).format(", ".join(changed_specs)),
			title=_("Naming Order Locked"),
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
