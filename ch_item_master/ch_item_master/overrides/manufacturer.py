# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Manufacturer doctype
Adds auto-increment logic for manufacturer_id
"""

import frappe


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
