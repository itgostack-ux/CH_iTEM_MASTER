# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Brand doctype
Adds auto-increment logic for brand_id and manufacturer immutability.
"""

import frappe
from frappe import _

from ch_item_master.ch_item_master.exceptions import ManufacturerChangeBlockedError


def before_insert(doc, method=None):
	"""Auto-generate brand_id if not set, with advisory lock for concurrency."""
	# Normalize brand name
	if doc.brand:
		doc.brand = " ".join(doc.brand.split())
	if not doc.brand_id:
		lock_name = "ch_brand_autoname"
		frappe.db.sql("SELECT GET_LOCK(%s, 10)", lock_name)
		try:
			max_id = frappe.db.sql("""
				SELECT IFNULL(MAX(brand_id), 0) 
				FROM `tabBrand`
			""")[0][0]
			doc.brand_id = int(max_id) + 1
		finally:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name)


def before_save(doc, method=None):
	"""Prevent changing the manufacturer on an existing Brand.

	A brand belongs to exactly one manufacturer. Once set, changing it
	would orphan/corrupt CH Models that reference this brand+manufacturer
	combination.
	"""
	# Normalize brand name
	if doc.brand:
		doc.brand = " ".join(doc.brand.split())
	if doc.is_new() or not doc.ch_manufacturer:
		return

	old_manufacturer = frappe.db.get_value("Brand", doc.name, "ch_manufacturer")
	if old_manufacturer and old_manufacturer != doc.ch_manufacturer:
		frappe.throw(
			_("Cannot change manufacturer of Brand {0} from {1} to {2}. "
			  "A brand cannot be reassigned to a different manufacturer. "
			  "Create a new brand instead."
			).format(
				frappe.bold(doc.brand),
				frappe.bold(old_manufacturer),
				frappe.bold(doc.ch_manufacturer),
			),
			title=_("Manufacturer Change Blocked"),
			exc=ManufacturerChangeBlockedError,
		)
