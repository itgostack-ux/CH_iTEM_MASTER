# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from ch_item_master.id_sequences import next_numeric_id

from ch_item_master.ch_item_master.exceptions import (
	DuplicateManufacturerError,
	DuplicateSpecError,
	DuplicateSubCategoryError,
	InvalidHSNCodeError,
	InvalidItemNatureError,
	InvalidNameOrderError,
	ItemNatureLockedError,
	NamingOrderLockedError,
	SpecInUseError,
	SubCategoryInUseError,
	VariantFlagLockedError,
	VariantSpecRemovalError,
)


# Item natures that produce non-stock items (Service / Subscription).
_NON_STOCK_NATURES = {"Service", "Subscription"}
# Item natures that ALWAYS preserve user-entered Item Name (no auto-naming).
_CUSTOM_NAMED_NATURES = {"Simple Custom-Named", "Service", "Subscription", "Asset / Capital"}
# Item natures that may NOT have variant specifications.
_NO_VARIANT_NATURES = {"Service", "Subscription"}


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
		if self.sub_category_name:
			self.sub_category_name = " ".join(self.sub_category_name.split())

	def before_insert(self):
		"""Auto-generate sub_category_id.

		Must be in before_insert (not autoname) so it runs even when Data
		Import pre-sets doc.name from the CSV — Frappe skips autoname() in
		that case, leaving the Int field at its default 0 and triggering a
		UNIQUE constraint violation.
		"""
		if not self.sub_category_id:
			self.sub_category_id = next_numeric_id("sub_category")

	def validate(self):
		if self.sub_category_name:
			self.sub_category_name = " ".join(self.sub_category_name.split())
		self._populate_ids()
		self._auto_fill_hsn_from_item_group()
		self._sync_is_variant_from_spec_type()
		self._validate_item_nature_contract()
		self.validate_unique_name_per_category()
		self.validate_case_insensitive_duplicate()
		self.validate_duplicate_manufacturers()
		self.validate_duplicate_specs()
		self.validate_name_order_sequential()
		self.validate_spec_changes_after_items_exist()
		self.validate_hsn_code()
		self._normalise_prefix()
		self._ensure_gst_item_tax_templates()

	def _populate_ids(self):
		"""Copy numeric IDs from linked master records for API."""
		self.category_id = 0
		self.item_group_id = 0
		if self.category:
			cat_data = frappe.db.get_value(
				"CH Category", self.category,
				["category_id", "item_group"], as_dict=True
			)
			if cat_data:
				self.category_id = cat_data.category_id or 0
				if not self.item_group:
					self.item_group = cat_data.item_group
			if self.item_group:
				self.item_group_id = frappe.db.get_value(
					"Item Group", self.item_group, "item_group_id"
				) or 0

	def _sync_is_variant_from_spec_type(self):
		"""Keep the hidden is_variant Check in sync with the user-facing spec_type Select.

		This keeps backward compatibility (all existing code reads is_variant)
		while giving users a clearer 'Variant / Property' label in the UI (FIX-7).

		TC_014: affects_price / in_item_name / name_order are user-configurable
		on BOTH Variant and Property rows (Property specs may still appear in
		the item name — see generate_item_name(), which filters only on
		in_item_name). Do NOT zero them out here.
		"""
		for row in self.specifications or []:
			if row.spec_type == "Variant":
				row.is_variant = 1
			elif row.spec_type == "Property":
				row.is_variant = 0
			else:
				# Backfill spec_type from is_variant for legacy rows (no spec_type set)
				row.spec_type = "Variant" if row.is_variant else "Property"

	def _validate_item_nature_contract(self):
		"""Enforce the item_nature contract.

		Rules:
		  - item_nature is required (default 'Variant Template' from JSON).
		  - Service / Subscription cannot have variant specs and must default
		    is_stock_item_default = 0.
		  - allow_custom_item_name is force-set on natures that require it
		    so existing controllers behave correctly.
		  - Changing item_nature after items exist is restricted to safe
		    transitions only.
		"""
		nature = (self.item_nature or "").strip()
		if not nature:
			raise InvalidItemNatureError(
				_("Item Nature is required on Sub Category {0}.").format(
					self.name or self.sub_category_name
				)
			)

		# Force allow_custom_item_name to mirror the chosen nature so that
		# downstream Item naming logic Just Works without each call site
		# duplicating the rule.
		self.allow_custom_item_name = 1 if nature in _CUSTOM_NAMED_NATURES else 0

		# Service / Subscription -> must be non-stock and must not carry variant specs.
		if nature in _NO_VARIANT_NATURES:
			has_variant_spec = any(
				(row.is_variant or 0) for row in (self.specifications or [])
			)
			if has_variant_spec:
				raise InvalidItemNatureError(
					_(
						"Sub Category {0}: Item Nature '{1}' cannot have Variant"
						" specifications. Remove variant specs or change Item Nature."
					).format(self.name or self.sub_category_name, nature)
				)
			self.is_stock_item_default = 0

		# Lock check: prevent dangerous transitions when items already exist.
		if not self.is_new() and self.has_value_changed("item_nature"):
			before = self.get_doc_before_save()
			old_nature = before.get("item_nature") if before else None
			if old_nature and old_nature != nature and self._has_existing_items():
				safe_transitions = {
					("Simple Auto-Named", "Simple Custom-Named"),
					("Simple Custom-Named", "Simple Auto-Named"),
				}
				if (old_nature, nature) not in safe_transitions:
					# Use frappe.throw (not bare raise) so the desk client
					# receives a rendered dialog — a bare raise on a
					# ValidationError subclass returns HTTP 417 with empty
					# _server_messages and the save fails silently.
					frappe.throw(
						_(
							"Cannot change Item Nature from '{0}' to '{1}' on Sub"
							" Category {2}: items already exist. Allowed transitions:"
							" Simple Auto-Named <-> Simple Custom-Named."
						).format(old_nature, nature, self.name),
						exc=ItemNatureLockedError,
						title=_("Item Nature Locked"),
					)

	def _has_existing_items(self):
		"""True if any Item is linked to this sub-category."""
		return bool(frappe.db.exists("Item", {"ch_sub_category": self.name}))

	def _auto_fill_hsn_from_item_group(self):
		"""Auto-fill HSN from Item Group if not already set.

		Hierarchy: Sub Category → Category → Item Group → gst_hsn_code.
		If the linked Item Group has an HSN code (from India Compliance)
		and this sub-category's hsn_code is empty, auto-populate it.
		"""
		if self.hsn_code or not self.category:
			return
		item_group = frappe.db.get_value("CH Category", self.category, "item_group")
		if not item_group:
			return
		hsn = frappe.db.get_value("Item Group", item_group, "gst_hsn_code")
		if hsn:
			self.hsn_code = hsn

	def _ensure_gst_item_tax_templates(self):
		"""Provision an Item Tax Template per India company for this Sub
		Category's (hsn_code, gst_rate) and wire it into GST HSN Code's
		`taxes` table so India Compliance's standard `set_taxes_from_hsn_code`
		hook propagates the right template to every Item created under this
		Sub Category — and to existing Items via `_cascade_tax_to_items`.

		Best-effort: never blocks Sub Category save on infrastructure issues
		(missing GST Settings rows, partially configured companies, etc.) —
		instead logs and continues. The auto-template flow is a convenience
		layer; transactions still throw later via `gst_utils.get_item_gst_rates`
		if an Item ends up without a template, surfacing the misconfiguration
		at the right boundary.
		"""
		if not self.hsn_code:
			return
		# Lazy import: avoids loading india_compliance during early bench
		# bootstrap (e.g. install patches) where IC may not yet be importable.
		try:
			from ch_item_master.ch_item_master.gst_template import (
				ensure_templates_for_subcategory,
			)
		except Exception:
			frappe.logger("ch_item_master").info(
				"_ensure_gst_item_tax_templates: gst_template module not importable yet — skipping"
			)
			return
		try:
			ensure_templates_for_subcategory(self)
		except Exception:
			# We never want to block a Sub Category save because GST
			# templates couldn't be wired. Log + continue.
			frappe.log_error(
				title=f"GST Item Tax Template auto-provision failed for {self.name}",
				message=frappe.get_traceback(),
			)

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
			if (old.in_item_name != row.in_item_name
					or str(old.name_order or 0) != str(row.name_order or 0)
					or old.is_mandatory != row.is_mandatory):
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
				title=_("Naming Order Locked"),
				exc=NamingOrderLockedError,
			)

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

	def _normalise_prefix(self):
		"""Force prefix to uppercase and strip whitespace."""
		if self.prefix:
			self.prefix = self.prefix.strip().upper()

	def on_update(self):
		"""Cascade a changed hsn_code or gst_rate to every linked Item.

		Tax is now authoritatively owned by Sub Category — Item.gst_hsn_code
		is read-only whenever ch_sub_category is set (see ch_erp15's
		Item-gst_hsn_code Property Setter) and is only ever populated FROM
		the Sub Category at creation time (overrides/item.py). If the Sub
		Category's hsn_code OR gst_rate changes later, every Item/Template/
		Variant already created under it would otherwise be left stale — this
		closes that gap.

		When `gst_rate` changes we ALSO re-run the Item Tax Template auto-
		provisioner so the new rate gets a fresh per-company template, the
		HSN code's `taxes` table is updated, and the cascade to existing
		Items picks up the new template via the standard
		`set_taxes_from_hsn_code` path.
		"""
		if self.is_new():
			return
		hsn_changed = self.has_value_changed("hsn_code")
		rate_changed = self.has_value_changed("gst_rate")
		if not (hsn_changed or rate_changed):
			return

		if rate_changed:
			# Re-provision per-company templates and re-wire HSN linkage so
			# the cascade below picks up rows pointing at the right template.
			try:
				self._ensure_gst_item_tax_templates()
			except Exception:
				frappe.log_error(
					title=f"GST template re-provision failed for Sub Category {self.name}",
					message=frappe.get_traceback(),
				)

		# Effective-dated cascade strategy (mirrors SAP condition records /
		# Oracle effective-dated tax rates):
		#   - HSN code change  → wholesale replace Item.taxes (different
		#     product classification; previous rows are no longer valid).
		#   - GST rate change  → APPEND rows pointing at the new template
		#     stamped with `valid_from = self.gst_rate_valid_from` (or today
		#     when blank). Old rows stay so historical transactions still
		#     resolve to the prior rate. ERPNext's `_get_tax_template_for_item()`
		#     picks the latest `valid_from` row that is ≤ posting_date, so
		#     this is honoured automatically at PO/PR/PI/SI validate time.
		#     A future `valid_from` lets admins schedule a rate change in
		#     advance (mirrors SAP MWST condition validity periods and
		#     Oracle Fusion effective-dated tax rates).
		mode = "replace" if hsn_changed else "append_effective_today"
		valid_from = self.get("gst_rate_valid_from") if rate_changed else None
		updated = self._cascade_tax_to_items(mode=mode, valid_from=valid_from)
		if updated:
			what = "Tax" if rate_changed and not hsn_changed else "Tax/HSN code"
			suffix = ""
			if rate_changed and valid_from:
				suffix = _(" (effective {0})").format(frappe.utils.formatdate(valid_from))
			frappe.msgprint(
				_("{0} updated on {1} Item(s) linked to this Sub Category{2}.").format(what, updated, suffix),
				indicator="green",
				alert=True,
			)

	def _cascade_tax_to_items(self, mode: str = "replace", valid_from=None):
		"""Bulk-propagate self.hsn_code (and the Item Tax Template rows
		that HSN code carries) to every Item where ch_sub_category == self.name.

		``mode`` controls how existing rows are treated:
		    - ``"replace"`` (default): drop all current Item.taxes rows and
		      rebuild from `GST HSN Code.taxes`. Use when the HSN code itself
		      changed (different product classification) — historical rows
		      are no longer valid.
		    - ``"append_effective_today"``: keep existing rows, add new ones
		      for templates not yet present, with ``valid_from = today`` (or
		      ``valid_from`` arg when provided). Used on `gst_rate` change so
		      historical documents still resolve at the old rate while new
		      documents (posting_date ≥ valid_from) use the new rate. Mirrors
		      SAP condition-record validity / Oracle effective-dated tax
		      rates / ERPNext's own ``_get_tax_template_for_item()`` picker.

		``valid_from`` (only honoured in append mode): explicit effective date
		for the newly-appended rows. ``None`` → today. A future date schedules
		the new rate to kick in from that date onwards; past dates back-date
		corrections.

		Deliberately uses raw SQL / bulk_insert instead of looping
		``frappe.get_doc("Item", ...).save()`` — Item.validate()/before_save()
		carry heavy, unrelated side effects (governance/approval gates,
		mandatory-spec validation, display-name regeneration, price sync)
		that must not re-fire just because a tax setting changed elsewhere,
		and could throw mid-cascade for reasons that have nothing to do with
		tax. Same bulk-SQL-cascade precedent as ``backfill_ids.py``.

		Mirrors india_compliance's ``set_taxes_from_hsn_code`` (which does
		this per-item at Item insert time) but applied to many existing
		items at once: GST HSN Code's own ``taxes`` child table uses the
		exact same child doctype (``Item Tax``) as ``Item.taxes``, so the
		rows are copied as-is, not re-derived.

		Returns the number of Items updated.
		"""
		from frappe.utils import nowdate

		item_codes = frappe.db.sql_list(
			"""
			SELECT name
			FROM `tabItem`
			WHERE ch_sub_category = %s
			ORDER BY name
			FOR UPDATE
			""",
			self.name,
		)
		if not item_codes:
			return 0

		now = now_datetime()

		frappe.db.sql(
			"UPDATE `tabItem` SET gst_hsn_code = %s, modified = %s WHERE ch_sub_category = %s",
			(self.hsn_code or "", now, self.name),
		)

		if mode == "replace":
			# Wholesale replacement — drop the old tax template rows for
			# every affected item before re-inserting from the HSN code.
			frappe.db.delete("Item Tax", {"parenttype": "Item", "parent": ["in", item_codes]})

		if not self.hsn_code:
			frappe.clear_cache(doctype="Item")
			return len(item_codes)

		hsn_tax_rows = frappe.get_all(
			"Item Tax",
			filters={"parenttype": "GST HSN Code", "parent": self.hsn_code},
			fields=["item_tax_template", "tax_category", "valid_from",
					"minimum_net_rate", "maximum_net_rate"],
			order_by="idx asc",
		)
		if not hsn_tax_rows:
			frappe.clear_cache(doctype="Item")
			return len(item_codes)

		# Effective-dated append mode: filter HSN rows down to ones whose
		# `(item_tax_template, tax_category)` is NOT already on each Item,
		# and stamp `valid_from` so the new rate kicks in from that date
		# onwards while historical transactions still resolve via the
		# older rows.
		valid_from_override = (valid_from or nowdate()) if mode == "append_effective_today" else None

		user = frappe.session.user
		fields = [
			"name", "parent", "parenttype", "parentfield", "idx",
			"item_tax_template", "tax_category", "valid_from",
			"minimum_net_rate", "maximum_net_rate",
			"creation", "modified", "owner", "modified_by", "docstatus",
		]

		if mode == "append_effective_today":
			# Pre-load existing (item, template, tax_category) tuples so we
			# don't append duplicates when the rate didn't actually move.
			existing = frappe.db.sql(
				"""
				SELECT parent, item_tax_template, COALESCE(tax_category, '') AS tax_category
				FROM `tabItem Tax`
				WHERE parenttype = 'Item' AND parent IN %(items)s
				""",
				{"items": tuple(item_codes)},
				as_dict=True,
			)
			seen = {
				(r.parent, r.item_tax_template, r.tax_category) for r in existing
			}
			# Find max(idx) per item so appended rows don't collide.
			max_idx_rows = frappe.db.sql(
				"""
				SELECT parent, MAX(idx) AS max_idx
				FROM `tabItem Tax`
				WHERE parenttype = 'Item' AND parent IN %(items)s
				GROUP BY parent
				""",
				{"items": tuple(item_codes)},
				as_dict=True,
			)
			max_idx = {r.parent: int(r.max_idx or 0) for r in max_idx_rows}

			values = []
			for item_code in item_codes:
				next_idx = max_idx.get(item_code, 0) + 1
				for tax_row in hsn_tax_rows:
					key = (item_code, tax_row.item_tax_template, tax_row.tax_category or "")
					if key in seen:
						continue
					values.append((
						frappe.generate_hash(length=10), item_code, "Item", "taxes", next_idx,
						tax_row.item_tax_template, tax_row.tax_category, valid_from_override,
						tax_row.minimum_net_rate, tax_row.maximum_net_rate,
						now, now, user, user, 0,
					))
					next_idx += 1
		else:
			# Replace mode: every item gets a fresh copy of every HSN row.
			values = [
				(
					frappe.generate_hash(length=10), item_code, "Item", "taxes", idx,
					tax_row.item_tax_template, tax_row.tax_category, tax_row.valid_from,
					tax_row.minimum_net_rate, tax_row.maximum_net_rate,
					now, now, user, user, 0,
				)
				for item_code in item_codes
				for idx, tax_row in enumerate(hsn_tax_rows, start=1)
			]

		if values:
			frappe.db.bulk_insert("Item Tax", fields, values)

		# Item.taxes / gst_hsn_code are read via cached doc/value lookups in
		# several core + India Compliance code paths during transaction entry.
		frappe.clear_cache(doctype="Item")
		return len(item_codes)

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

	def after_rename(self, old, new, merge=False):
		"""Keep sub_category_name in sync with the document name
		(name format: {category}-{sub_category_name}) and cascade
		the rename to CH Model documents whose name embeds this
		Sub Category as a prefix."""
		category = frappe.db.get_value("CH Sub Category", new, "category")
		prefix = (category or "") + "-"
		if category and new.startswith(prefix):
			new_sub_name = new[len(prefix):]
			frappe.db.set_value(
				"CH Sub Category", new, "sub_category_name",
				new_sub_name, update_modified=False,
			)

		# Cascade to Models
		models = frappe.get_all(
			"CH Model",
			filters={"sub_category": new},
			pluck="name",
		)
		old_prefix = old + "-"
		for model_name in models:
			if model_name.startswith(old_prefix):
				new_model_name = new + model_name[len(old):]
				frappe.rename_doc("CH Model", model_name, new_model_name)

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
