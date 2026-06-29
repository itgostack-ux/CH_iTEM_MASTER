# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Monitoring Number Cards for the Item Master governance dashboard.

Idempotent — runs from after_migrate.
"""

import frappe

_NUMBER_CARDS = [
	{
		"name": "Items Pending Review",
		"label": "Items Pending Review",
		"document_type": "Item",
		"function": "Count",
		"filters_json": '[["Item","ch_lifecycle_status","=","Pending Review",false]]',
		"color": "#1f8efa",
	},
	{
		"name": "Items Blocked",
		"label": "Items Blocked",
		"document_type": "Item",
		"function": "Count",
		"filters_json": '[["Item","ch_lifecycle_status","=","Blocked",false]]',
		"color": "#e24c4c",
	},
	{
		"name": "Items Obsolete",
		"label": "Items Obsolete",
		"document_type": "Item",
		"function": "Count",
		"filters_json": '[["Item","ch_lifecycle_status","=","Obsolete",false]]',
		"color": "#7c7c7c",
	},
	{
		"name": "Items Missing HSN",
		"label": "Items Missing HSN",
		"document_type": "Item",
		"function": "Count",
		"filters_json": '[["Item","gst_hsn_code","is","not set",false]]',
		"color": "#f0a500",
	},
	{
		"name": "Audit Events Today",
		"label": "Audit Events Today",
		"document_type": "CH Item Audit Log",
		"function": "Count",
		"filters_json": '[["CH Item Audit Log","changed_on","Timespan","today"]]',
		"color": "#2da847",
	},
]


def install_number_cards() -> None:
	if not frappe.db.exists("DocType", "Number Card"):
		return  # Older Frappe — skip silently.

	for card in _NUMBER_CARDS:
		if frappe.db.exists("Number Card", card["name"]):
			doc = frappe.get_doc("Number Card", card["name"])
			doc.update(card)
			doc.is_public = 1
			doc.save(ignore_permissions=True)
			continue
		try:
			doc = frappe.new_doc("Number Card")
			doc.update(card)
			doc.is_public = 1
			doc.insert(ignore_permissions=True, set_name=card["name"])
		except Exception:
			frappe.log_error(
				title=f"Number Card install failed: {card['name']}",
				message=frappe.get_traceback(),
			)
