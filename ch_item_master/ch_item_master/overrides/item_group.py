# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Item Group doctype.
Adds auto-increment logic for item_group_id (API integration).
"""

import frappe


def before_insert(doc, method=None):
	"""Auto-generate item_group_id if not set, with advisory lock for concurrency."""
	if not doc.item_group_id:
		lock_name = "ch_item_group_autoname"
		frappe.db.sql("SELECT GET_LOCK(%s, 10)", lock_name)
		try:
			max_id = frappe.db.sql("""
				SELECT IFNULL(MAX(item_group_id), 0)
				FROM `tabItem Group`
			""")[0][0]
			doc.item_group_id = int(max_id) + 1
		finally:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name)
