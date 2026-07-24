# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Item Group doctype.
Adds auto-increment logic for item_group_id (API integration).
"""

import frappe

from ch_item_master.id_sequences import next_numeric_id


def before_insert(doc, method=None):
	"""Auto-generate the atomic Item Group integration ID."""
	if not doc.item_group_id:
		doc.item_group_id = next_numeric_id("item_group")
