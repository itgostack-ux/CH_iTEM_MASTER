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
	# Normalize name fields
	if doc.short_name:
		doc.short_name = " ".join(doc.short_name.split())
	if doc.full_name:
		doc.full_name = " ".join(doc.full_name.split())
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
	"""Normalize name fields and warn if full_name is missing."""
	# Normalize name fields
	if doc.short_name:
		doc.short_name = " ".join(doc.short_name.split())
	if doc.full_name:
		doc.full_name = " ".join(doc.full_name.split())

	# Skip full_name check during rename (Frappe internally saves the doc)
	if doc.flags.name_changed:
		return

	if not doc.full_name:
		frappe.msgprint(
			_("Full Name is recommended for Manufacturer {0}. "
			  "Please enter the full legal name."
			).format(frappe.bold(doc.short_name)),
			indicator="orange",
			title=_("Missing Full Name"),
		)
