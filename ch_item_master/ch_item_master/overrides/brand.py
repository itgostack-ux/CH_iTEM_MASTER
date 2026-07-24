# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Brand doctype
Adds auto-increment logic for brand_id and manufacturer list validation.
"""

import frappe
from frappe import _

from ch_item_master.id_sequences import next_numeric_id


def before_insert(doc, method=None):
	"""Auto-generate brand_id if not set, with advisory lock for concurrency."""
	# Normalize brand name
	if doc.brand:
		doc.brand = " ".join(doc.brand.split())
	if not doc.brand_id:
		doc.brand_id = next_numeric_id("brand")


def before_save(doc, method=None):
	"""Validate manufacturers list and populate manufacturer_id."""
	# Normalize brand name
	if doc.brand:
		doc.brand = " ".join(doc.brand.split())

	# Validate no duplicate manufacturers in the child table
	seen = set()
	manufacturers = []
	for row in (doc.get("ch_manufacturers") or []):
		if row.manufacturer in seen:
			frappe.throw(
				_("Row #{0}: Manufacturer {1} is already listed. "
				  "Each manufacturer should appear only once."
				).format(row.idx, frappe.bold(row.manufacturer)),
				title=_("Duplicate Manufacturer"),
			)
		seen.add(row.manufacturer)
		manufacturers.append(row.manufacturer)

	# Batch-fetch manufacturer_ids in one query instead of N+1
	if manufacturers:
		mfr_ids = {
			r.name: r.manufacturer_id
			for r in frappe.get_all(
				"Manufacturer",
				filters={"name": ("in", manufacturers)},
				fields=["name", "manufacturer_id"],
			)
		}
		for row in doc.get("ch_manufacturers") or []:
			row.manufacturer_id = mfr_ids.get(row.manufacturer, 0) or 0
