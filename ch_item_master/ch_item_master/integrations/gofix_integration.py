# Copyright (c) 2026, GoStack and contributors
# INT-1 + INT-2: GoFix → CH Warranty Claim + Serial Lifecycle integration
#
# This module handles bi-directional sync between GoFix Service Requests
# and CH Warranty Claims / CH Serial Lifecycle.

import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime


# Map GoFix Service Request statuses to warranty claim statuses
SR_TO_CLAIM_STATUS = {
	"Completed": "Repair Complete",
	"Invoiced": "Repair Complete",
	"Delivered": "Delivered",
	"Cancelled": "Cancelled",
	"Rejected": "Rejected",
}

# Map GoFix statuses to Serial Lifecycle statuses
SR_TO_LIFECYCLE_STATUS = {
	"Completed": "In Stock",    # Repaired, back in stock pending delivery
	"Delivered": "Sold",        # Delivered back to customer
	"Cancelled": "In Stock",    # Cancelled, device goes back to stock
}


def on_service_request_update(doc, method=None):
	"""Called on Service Request on_update. Syncs status to warranty claim + lifecycle.

	Error-isolated so a failure here never blocks the primary GoFix
	workflow that owns this doctype.
	"""
	if doc.flags.get("skip_claim_sync"):
		return

	try:
		_sync_warranty_claim(doc)
	except Exception:
		frappe.log_error(
			title=f"GoFix→Warranty Claim sync error: {doc.name}",
			message=frappe.get_traceback(),
		)

	try:
		_sync_serial_lifecycle(doc)
	except Exception:
		frappe.log_error(
			title=f"GoFix→Serial Lifecycle sync error: {doc.name}",
			message=frappe.get_traceback(),
		)


def _sync_warranty_claim(doc):
	"""INT-1: Update CH Warranty Claim status when GoFix Service Request status changes."""
	sr_name = doc.name
	sr_status = doc.status or doc.decision

	if not sr_status:
		return

	# Find linked warranty claim — claim stores service_request = SR name
	claim_name = frappe.db.get_value(
		"CH Warranty Claim",
		{"service_request": sr_name, "docstatus": 1},
		"name",
	)
	if not claim_name:
		return

	new_claim_status = SR_TO_CLAIM_STATUS.get(sr_status)
	if not new_claim_status:
		return

	# Check current claim status to avoid unnecessary updates
	current = frappe.db.get_value("CH Warranty Claim", claim_name, "claim_status")
	if current == new_claim_status:
		return

	# Terminal statuses should not be overwritten
	if current in ("Closed", "Cancelled", "Delivered"):
		return

	# Update claim
	frappe.db.set_value(
		"CH Warranty Claim", claim_name,
		{
			"claim_status": new_claim_status,
			"repair_status": _map_repair_status(sr_status),
		},
		update_modified=True,
	)

	# Log the status change
	try:
		claim = frappe.get_doc("CH Warranty Claim", claim_name)
		claim._log(
			f"GoFix Sync: {sr_status}",
			current,
			new_claim_status,
			f"Auto-updated from Service Request {sr_name} status: {sr_status}",
		)
	except Exception:
		frappe.log_error(
			f"Failed to log claim status change for {claim_name}",
			"GoFix → Warranty Claim Sync",
		)


def _sync_serial_lifecycle(doc):
	"""INT-2: Update CH Serial Lifecycle when GoFix Service Request status changes."""
	sr_status = doc.status or doc.decision
	serial_no = doc.serial_no

	if not serial_no or not sr_status:
		return

	new_lifecycle_status = SR_TO_LIFECYCLE_STATUS.get(sr_status)
	if not new_lifecycle_status:
		# For in-progress statuses, set to "In Service"
		if sr_status in ("Accepted", "In Service", "In Progress", "Repair In Progress"):
			new_lifecycle_status = "In Service"
		else:
			return

	# Check if CH Serial Lifecycle exists
	if not frappe.db.exists("CH Serial Lifecycle", serial_no):
		return

	current_status = frappe.db.get_value(
		"CH Serial Lifecycle", serial_no, "lifecycle_status"
	)
	if current_status == new_lifecycle_status:
		return

	# Terminal statuses should not be overwritten
	if current_status in ("Scrapped", "Lost"):
		return

	try:
		lc = frappe.get_doc("CH Serial Lifecycle", serial_no)
		lc.lifecycle_status = new_lifecycle_status
		lc.notes = f"GoFix Service Request {doc.name}: {sr_status}"
		lc.flags.ignore_permissions = True
		lc.save()
	except Exception:
		frappe.log_error(
			f"Failed to update lifecycle for {serial_no} from SR {doc.name}",
			"GoFix → Serial Lifecycle Sync",
		)


def _map_repair_status(sr_status):
	"""Map Service Request status to warranty claim repair_status."""
	mapping = {
		"Completed": "Completed",
		"Invoiced": "Completed",
		"Delivered": "Completed",
		"Cancelled": "Cancelled",
		"Rejected": "Cancelled",
		"In Service": "In Progress",
		"In Progress": "In Progress",
		"Accepted": "Pending",
	}
	return mapping.get(sr_status, "In Progress")
