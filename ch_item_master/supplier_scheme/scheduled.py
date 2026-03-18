"""Scheduled tasks for the Supplier Scheme module."""

import frappe
from frappe.utils import getdate, nowdate


def auto_close_expired_schemes():
	"""Auto-close schemes whose valid_to has passed."""
	today = getdate(nowdate())
	expired = frappe.get_all(
		"Supplier Scheme Circular",
		filters={
			"docstatus": 1,
			"status": "Active",
			"valid_to": ("<", today),
		},
		pluck="name",
	)

	for scheme_name in expired:
		frappe.db.set_value("Supplier Scheme Circular", scheme_name, "status", "Closed")
		frappe.logger("supplier_scheme").info(f"Auto-closed expired scheme {scheme_name}")

	if expired:
		frappe.db.commit()
