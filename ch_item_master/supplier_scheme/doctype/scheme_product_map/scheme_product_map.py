"""Scheme Product Map — persistent mapping of supplier product names to internal items.

Maps supplier scheme document product names (e.g. "Galaxy S24 Ultra 12+256GB")
to internal Item codes, CH Models, or Item Groups so the scheme engine
can reliably match POS sales.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate

from ch_item_master.security import require_scoped_document_action


_SCHEME_MAPPING_ROLES = ("Accounts Manager", "Purchase Manager", "Scheme Manager")


class SchemeProductMap(Document):
	_VERIFICATION_CONTEXT = object()
	_MAPPING_FIELDS = (
		"scheme",
		"company",
		"supplier_product_name",
		"brand",
		"match_level",
		"item_code",
		"model",
		"item_group",
	)

	def _authorize_verification(self):
		self.flags.scheme_map_verification_context = self._VERIFICATION_CONTEXT

	def _has_verification_context(self):
		return self.flags.get("scheme_map_verification_context") is self._VERIFICATION_CONTEXT

	def _validate_verification_evidence(self):
		if self._has_verification_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			if self.mapping_source == "Verified" or self.verified_by or self.verified_on:
				frappe.throw(_("Mapping verification evidence is server-managed."), frappe.PermissionError)
			return
		if (
			self.verified_by != before.verified_by
			or self.verified_on != before.verified_on
			or (self.mapping_source == "Verified" and before.mapping_source != "Verified")
		):
			frappe.throw(
				_("Use Mark Verified to record mapping verification."),
				frappe.PermissionError,
			)
		if before.mapping_source == "Verified" and any(
			self.get(fieldname) != before.get(fieldname) for fieldname in self._MAPPING_FIELDS
		):
			self.mapping_source = "Manual"
			self.verified_by = None
			self.verified_on = None

	def validate(self):
		self._validate_verification_evidence()
		self._validate_company()
		self._validate_mapping_target()
		self._compute_item_count()
		self._check_duplicate()

	def _validate_company(self):
		if not self.scheme:
			return
		scheme = frappe.db.get_value(
			"Supplier Scheme Circular", self.scheme, ["name", "company"], as_dict=True
		)
		if not scheme:
			frappe.throw(_("Supplier Scheme Circular {0} was not found.").format(self.scheme))
		if self.company and self.company != scheme.company:
			frappe.throw(_("Product map company must match the linked supplier scheme."))
		self.company = scheme.company

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
			"company": self.company if self.company else ("is", "not set"),
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

	@frappe.whitelist(methods=["POST"])
	def mark_verified(self) -> dict:
		"""Mark this mapping as verified by the current user."""
		if not self.company:
			frappe.throw(
				_("Set a company before verifying this product mapping."),
				frappe.ValidationError,
			)
		require_scoped_document_action(
			self,
			"supplier_scheme_management_roles",
			_SCHEME_MAPPING_ROLES,
			action=_("verify a supplier scheme product mapping"),
			permission_types=("write",),
			company_field="company",
			lock=True,
		)
		self.mapping_source = "Verified"
		self.verified_by = frappe.session.user
		self.verified_on = nowdate()
		self._authorize_verification()
		self.save()
		return {"status": "ok"}
