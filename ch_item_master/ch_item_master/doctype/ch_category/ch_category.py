# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHCategory(Document):
	def autoname(self):
		"""Auto-generate category_id before insert"""
		if not self.category_id:
			# Get next ID from sequence
			last_id = frappe.db.sql("""
				SELECT COALESCE(MAX(category_id), 0) 
				FROM `tabCH Category`
			""")[0][0]
			self.category_id = (last_id or 0) + 1

	def validate(self):
		self._validate_item_group_not_reused()
		self._validate_deactivation()

	def _validate_item_group_not_reused(self):
		"""Prevent two categories from pointing to the same Item Group.

		Each CH Category maps 1:1 to an ERPNext Item Group — reusing one
		would cause items to appear in the wrong category tree.
		"""
		if not self.item_group:
			return
		existing = frappe.db.get_value(
			"CH Category",
			{"item_group": self.item_group, "name": ("!=", self.name)},
			"name",
		)
		if existing:
			frappe.throw(
				_("Item Group {0} is already linked to Category {1}. "
				  "Each Item Group can only belong to one CH Category."
				).format(frappe.bold(self.item_group), frappe.bold(existing)),
				title=_("Duplicate Item Group Mapping"),
			)

	def _validate_deactivation(self):
		"""Warn before deactivating a category that has active sub-categories."""
		if self.is_new() or self.is_active:
			return

		before = self.get_doc_before_save()
		if not before or not before.is_active:
			return  # was already inactive

		active_subs = frappe.db.count(
			"CH Sub Category",
			{"category": self.name, "is_active": 1},
		)
		if active_subs:
			frappe.msgprint(
				_("This category has {0} active sub-categor(ies). "
				  "They will remain active but won't appear under any active category. "
				  "Consider deactivating them first."
				).format(frappe.bold(str(active_subs))),
				indicator="orange",
				title=_("Active Sub-Categories Exist"),
			)

	def on_trash(self):
		"""Block deletion if sub-categories exist."""
		sub_count = frappe.db.count("CH Sub Category", {"category": self.name})
		if sub_count:
			frappe.throw(
				_("Cannot delete Category {0} — {1} sub-categor(ies) depend on it. "
				  "Delete or reassign the sub-categories first."
				).format(frappe.bold(self.category_name), sub_count),
				title=_("Category In Use"),
			)
