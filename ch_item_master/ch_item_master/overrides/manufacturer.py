# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Manufacturer doctype
Adds auto-increment logic for manufacturer_id and mandatory full_name.
"""

import frappe
from frappe import _


def before_insert(doc, method=None):
	"""Auto-generate manufacturer_id if not set, with advisory lock for concurrency."""
	if not doc.manufacturer_id:
		lock_name = "ch_manufacturer_autoname"
		frappe.db.sql("SELECT GET_LOCK(%s, 10)", lock_name)
		try:
			max_id = frappe.db.sql("""
				SELECT IFNULL(MAX(manufacturer_id), 0) 
				FROM `tabManufacturer`
			""")[0][0]
			doc.manufacturer_id = int(max_id) + 1
		finally:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name)


def before_save(doc, method=None):
	"""Validate that full_name is filled (mandatory for CH Item Master)."""
	if not doc.full_name:
		frappe.throw(
			_("Full Name is mandatory for Manufacturer {0}. "
			  "Please enter the full legal name."
			).format(frappe.bold(doc.short_name)),
			title=_("Missing Full Name"),
		)
