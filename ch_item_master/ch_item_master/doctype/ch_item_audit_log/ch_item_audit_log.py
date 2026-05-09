# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CHItemAuditLog(Document):
	"""Append-only audit log for governance-relevant changes to Item.

	Rows are written by ch_item_master.ch_item_master.governance.write_audit().
	Never edited from the UI — read/report only.
	"""
	pass
