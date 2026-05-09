# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Patch v8: Vendor Info governance hardening.

1) Remove broad "All" DocPerm from CH Vendor Info Record
2) Ensure role-based DocPerms for vendor access
3) Normalize preferred supplier flags to one active preferred supplier per item
"""

import frappe


def execute():
	_sync_vendor_docperms()
	_normalize_preferred_supplier_flags()
	frappe.db.commit()


def _sync_vendor_docperms():
	doctype = "CH Vendor Info Record"
	if not frappe.db.exists("DocType", doctype):
		return

	# Remove broad All role access if present
	for name in frappe.get_all(
		"DocPerm",
		filters={"parent": doctype, "parenttype": "DocType", "role": "All"},
		pluck="name",
	):
		frappe.delete_doc("DocPerm", name, ignore_permissions=True, force=True)

	required = [
		{
			"role": "CH Vendor Manager",
			"read": 1,
			"write": 1,
			"create": 1,
			"report": 1,
			"export": 1,
			"print": 1,
			"email": 1,
			"share": 1,
			"delete": 0,
		},
		{
			"role": "CH Master Manager",
			"read": 1,
			"write": 0,
			"create": 0,
			"report": 1,
			"export": 1,
			"print": 1,
			"email": 1,
			"share": 1,
			"delete": 0,
		},
		{
			"role": "CH Master Approver",
			"read": 1,
			"write": 0,
			"create": 0,
			"report": 1,
			"export": 1,
			"print": 1,
			"email": 1,
			"share": 1,
			"delete": 0,
		},
		{
			"role": "CH Viewer",
			"read": 1,
			"write": 0,
			"create": 0,
			"report": 1,
			"export": 1,
			"print": 1,
			"email": 1,
			"share": 1,
			"delete": 0,
		},
		{
			"role": "System Manager",
			"read": 1,
			"write": 1,
			"create": 1,
			"report": 1,
			"export": 1,
			"print": 1,
			"email": 1,
			"share": 1,
			"delete": 1,
		},
	]

	for spec in required:
		existing = frappe.db.exists(
			"DocPerm",
			{"parent": doctype, "parenttype": "DocType", "role": spec["role"], "permlevel": 0},
		)
		if existing:
			frappe.db.set_value("DocPerm", existing, spec, update_modified=False)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "DocPerm",
					"parent": doctype,
					"parenttype": "DocType",
					"parentfield": "permissions",
					"permlevel": 0,
					**spec,
				}
			)
			doc.insert(ignore_permissions=True)

	frappe.clear_cache(doctype=doctype)


def _normalize_preferred_supplier_flags():
	if not frappe.db.table_exists("CH Vendor Info Record"):
		return

	items = frappe.get_all(
		"CH Vendor Info Record",
		filters={"active": 1, "preferred": 1},
		fields=["item_code"],
		group_by="item_code",
	)

	for row in items:
		recs = frappe.get_all(
			"CH Vendor Info Record",
			filters={"item_code": row.item_code, "active": 1, "preferred": 1},
			fields=["name", "modified"],
			order_by="modified desc",
		)
		if len(recs) <= 1:
			continue

		# Keep the latest preferred record, unset others
		keep = recs[0].name
		for r in recs[1:]:
			frappe.db.set_value("CH Vendor Info Record", r.name, "preferred", 0, update_modified=False)

		frappe.logger().info(
			"v8 vendor hardening: normalized preferred suppliers for %s (kept %s)",
			row.item_code,
			keep,
		)
