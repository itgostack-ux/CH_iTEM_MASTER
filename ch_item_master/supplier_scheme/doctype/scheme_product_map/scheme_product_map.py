"""Scheme Product Map — persistent mapping of supplier product names to internal items.

Maps supplier scheme document product names (e.g. "Galaxy S24 Ultra 12+256GB")
to internal Item codes, CH Models, or Item Groups so the scheme engine
can reliably match POS sales.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate


class SchemeProductMap(Document):
	def validate(self):
		self._validate_mapping_target()
		self._compute_item_count()
		self._check_duplicate()

	def _validate_mapping_target(self):
		"""Ensure the correct target field is set for the match_level."""
		if self.match_level == "Item" and not self.item_code:
			frappe.throw(_("Item Code is required when Match Level is 'Item'"), title=_("Scheme Product Map Error"))
		if self.match_level == "Model" and not self.model:
			frappe.throw(_("CH Model is required when Match Level is 'Model'"), title=_("Scheme Product Map Error"))
		if self.match_level == "Item Group" and not self.item_group:
			frappe.throw(_("Item Group is required when Match Level is 'Item Group'"), title=_("Scheme Product Map Error"))

	def _compute_item_count(self):
		"""Count how many active Items match this mapping."""
		filters = {"disabled": 0}
		if self.brand:
			filters["brand"] = self.brand

		if self.match_level == "Item":
			self.mapped_item_count = 1 if frappe.db.exists("Item", self.item_code) else 0
		elif self.match_level == "Model":
			self.mapped_item_count = frappe.db.count(
				"Item", {**filters, "ch_model": self.model}
			)
		elif self.match_level == "Item Group":
			self.mapped_item_count = frappe.db.count(
				"Item", {**filters, "item_group": self.item_group}
			)
		else:
			self.mapped_item_count = 0

	def _check_duplicate(self):
		"""Warn if scheme + supplier_product_name (or brand + name) already exists (different record)."""
		filters = {
			"supplier_product_name": self.supplier_product_name,
			"name": ("!=", self.name),
		}
		if self.scheme:
			filters["scheme"] = self.scheme
		else:
			filters["brand"] = self.brand
		existing = frappe.db.get_value(
			"Scheme Product Map",
			filters,
			"name",
		)
		if existing:
			frappe.throw(
				_("A mapping for '{0}' under brand '{1}' already exists: {2}").format(
					self.supplier_product_name, self.brand, existing
				),
				exc=frappe.DuplicateEntryError,
			)

	@frappe.whitelist()
	def mark_verified(self) -> None:
		"""Mark this mapping as verified by the current user."""
		self.mapping_source = "Verified"
		self.verified_by = frappe.session.user
		self.verified_on = nowdate()
		self.save(ignore_permissions=True)
		return {"status": "ok"}
