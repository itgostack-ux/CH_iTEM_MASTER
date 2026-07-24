# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Overrides for ERPNext standard Manufacturer doctype
Adds auto-increment logic for manufacturer_id and mandatory full_name.
"""

import frappe
from frappe import _

from ch_item_master.id_sequences import next_numeric_id


def before_insert(doc, method=None):
	"""Auto-generate manufacturer_id if not set, with advisory lock for concurrency."""
	# Normalize name fields
	if doc.short_name:
		doc.short_name = " ".join(doc.short_name.split())
	if doc.full_name:
		doc.full_name = " ".join(doc.full_name.split())
	if not doc.manufacturer_id:
		doc.manufacturer_id = next_numeric_id("manufacturer")


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
