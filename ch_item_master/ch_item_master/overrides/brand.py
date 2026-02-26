# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Brand doctype
Adds auto-increment logic for brand_id
"""

import frappe


def before_insert(doc, method=None):
	"""Auto-generate brand_id if not set, with advisory lock for concurrency."""
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
