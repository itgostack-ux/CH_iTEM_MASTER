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
