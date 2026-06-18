# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Custom exception classes for CH Item Master.

Define here so they can be imported by controllers AND test files
for precise assertion matching (e.g. self.assertRaises(DuplicateCategoryError, ...)).
"""

import frappe


# ── CH Category ──────────────────────────────────────────────────────────────
class DuplicateCategoryError(frappe.ValidationError):
	pass


class CategoryInUseError(frappe.ValidationError):
	pass


# ── CH Sub Category ──────────────────────────────────────────────────────────
class DuplicateSubCategoryError(frappe.ValidationError):
	pass


class InvalidHSNCodeError(frappe.ValidationError):
	pass


class VariantFlagLockedError(frappe.ValidationError):
	pass


class SpecInUseError(frappe.ValidationError):
	pass


class SubCategoryInUseError(frappe.ValidationError):
	pass


class InvalidNameOrderError(frappe.ValidationError):
	pass


class DuplicateManufacturerError(frappe.ValidationError):
	pass


class DuplicateSpecError(frappe.ValidationError):
	pass


class VariantSpecRemovalError(frappe.ValidationError):
	pass


class NamingOrderLockedError(frappe.ValidationError):
	pass


class InvalidItemNatureError(frappe.ValidationError):
	"""Raised when item_nature is missing or set to a value that is incompatible
	with the sub-category's spec / behavior configuration."""
	pass


class ItemNatureLockedError(frappe.ValidationError):
	"""Raised when item_nature is changed after items already exist for the
	sub-category in a way that would break existing data."""
	pass


# ── Governance / Lifecycle ──────────────────────────────────────────────────
class ItemNotActiveError(frappe.ValidationError):
	"""Raised when a transaction tries to use an Item whose lifecycle_status is
	not 'Active' (Draft / Pending Review / Obsolete / Blocked)."""
	pass


class InvalidLifecycleTransitionError(frappe.ValidationError):
	"""Raised when lifecycle_status is changed in a non-allowed direction or
	by a user without the right approver role."""
	pass


class SoftDuplicateError(frappe.ValidationError):
	"""Raised when a strict duplicate-prevention check finds another Item with
	the same normalized signature (manufacturer + model + brand + name)."""
	pass


class IncompleteItemMasterError(frappe.ValidationError):
	"""Raised when an Item is activated without satisfying the completeness
	profile required by its item_nature."""
	pass


class ImportIdempotencyError(frappe.ValidationError):
	"""Raised when a bulk-import call replays an idempotency key already seen
	within the dedup window."""
	pass


# ── CH Model ─────────────────────────────────────────────────────────────────
class DuplicateModelError(frappe.ValidationError):
	pass


class ManufacturerNotAllowedError(frappe.ValidationError):
	pass


class BrandManufacturerMismatchError(frappe.ValidationError):
	pass


class InvalidSpecValueError(frappe.ValidationError):
	pass


class MissingSpecValuesError(frappe.ValidationError):
	pass


class ModelInUseError(frappe.ValidationError):
	pass


# ── CH Item Price ────────────────────────────────────────────────────────────
class InvalidPriceError(frappe.ValidationError):
	pass


class InvalidPriceHierarchyError(frappe.ValidationError):
	pass


class OverlappingPriceError(frappe.ValidationError):
	pass


# ── CH Item Offer ────────────────────────────────────────────────────────────
class InvalidOfferError(frappe.ValidationError):
	pass


class OverlappingOfferError(frappe.ValidationError):
	pass


# ── Item Overrides ───────────────────────────────────────────────────────────
class DuplicateTemplateError(frappe.ValidationError):
	pass


class DuplicateItemNameError(frappe.ValidationError):
	pass


class MissingPrefixError(frappe.ValidationError):
	pass


# ── Brand / Manufacturer Overrides ──────────────────────────────────────────
class ManufacturerChangeBlockedError(frappe.ValidationError):
	pass


# ── CH Warranty Plan ─────────────────────────────────────────────────────────
class InvalidValidityPeriodError(frappe.ValidationError):
	pass


class InactivePlanError(frappe.ValidationError):
	pass


# ── Active VAS Plans ─────────────────────────────────────────────────────────────
class DuplicateSoldPlanError(frappe.ValidationError):
	pass


class WarrantyExpiredError(frappe.ValidationError):
	pass


class MaxClaimsReachedError(frappe.ValidationError):
	pass


class WarrantyVoidError(frappe.ValidationError):
	pass


# ── MSP (Minimum Selling Price) ──────────────────────────────────────────────
class BelowMSPError(frappe.ValidationError):
	pass
