# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Brand doctype
Adds auto-increment logic for brand_id and manufacturer list validation.
"""

import frappe
from frappe import _


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
	"""Validate manufacturers list — no duplicates allowed."""
	# Normalize brand name
	if doc.brand:
		doc.brand = " ".join(doc.brand.split())

	# Validate no duplicate manufacturers in the child table
	seen = set()
	for row in (doc.get("ch_manufacturers") or []):
		if row.manufacturer in seen:
			frappe.throw(
				_("Row #{0}: Manufacturer {1} is already listed. "
				  "Each manufacturer should appear only once."
				).format(row.idx, frappe.bold(row.manufacturer)),
				title=_("Duplicate Manufacturer"),
			)
		seen.add(row.manufacturer)

