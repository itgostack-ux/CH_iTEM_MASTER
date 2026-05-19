# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Purchase Receipt hooks — auto-create Serial No + CH Serial Lifecycle
from IMEI Tracking child table on Purchase Receipt submit.

Flow:
  Purchase Receipt (submitted)
    └─ custom_track (child table: IMEI Tracking)
         └─ For each IMEI row:
              1. Create/update ERPNext Serial No
              2. Create CH Serial Lifecycle with status "Received"
"""

import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime


def on_submit(doc, method):
	"""Create Serial No and CH Serial Lifecycle records from IMEI Tracking rows.

	Stamping of `Serial No.ch_is_imei` for items where the IMEI came in via
	PR Item.serial_no text is now owned by
	`ch_erp15.ch_erp15.custom.purchase_receipt.CustomPurchaseReceipt.on_submit`,
	which derives the flag from `Item.ch_serial_kind` (SAP Serial Number Profile
	equivalent). Having two writers using different source fields was a silent
	last-writer-wins bug that downgraded real IMEIs to barcodes; that path has
	been removed.

	This hook still handles the `custom_track` (IMEI Tracking) child-table flow
	which is the side-channel used when IMEIs are entered separately from the
	line-item serial_no text. Those are unambiguously real IMEIs.
	"""
	if not doc.get("custom_track"):
		return

	created_serials = []
	for row in doc.custom_track:
		if not row.imei_number:
			continue

		imei = row.imei_number.strip()
		item_code = row.item_code

		if not item_code:
			continue

		# Use IMEI as the serial number
		serial_no = imei

		# 1. Create ERPNext Serial No if it doesn't exist (advisory lock prevents duplicates)
		sn_lock_key = f"serial_create_{frappe.scrub(str(serial_no))}"
		sn_lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 10)", (sn_lock_key,))[0][0]
		if not sn_lock_result:
			frappe.log_error(
				f"Could not acquire lock for serial {serial_no}",
				"Serial Creation Lock Timeout",
			)
			continue
		try:
			if not frappe.db.exists("Serial No", serial_no):
				sn = frappe.new_doc("Serial No")
				sn.serial_no = serial_no
				sn.item_code = item_code
				sn.company = doc.company
				sn.warehouse = doc.set_warehouse or (doc.items[0].warehouse if doc.items else None)
				sn.reference_doctype = "Purchase Receipt"
				sn.reference_name = doc.name
				sn.posting_date = doc.posting_date
				sn.purchase_rate = _get_item_rate(doc, item_code)
				sn.status = "Active"
				sn.flags.ignore_permissions = True
				sn.insert()
		finally:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", (sn_lock_key,))

		# 2. Create CH Serial Lifecycle if it doesn't exist (advisory lock prevents duplicates)
		lc_lock_key = f"serial_create_{frappe.scrub(str(serial_no))}_lifecycle"
		lc_lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lc_lock_key,))[0][0]
		if not lc_lock_result:
			frappe.log_error(
				f"Could not acquire lifecycle lock for serial {serial_no}",
				"Serial Creation Lock Timeout",
			)
			continue
		try:
			if not frappe.db.exists("CH Serial Lifecycle", serial_no):
				lc = frappe.new_doc("CH Serial Lifecycle")
				lc.serial_no = serial_no
				lc.imei_number = imei
				lc.item_code = item_code
				lc.ch_serial_kind = "IMEI"
				lc.lifecycle_status = "Received"
				lc.current_company = doc.company
				lc.current_warehouse = doc.set_warehouse or (
					doc.items[0].warehouse if doc.items else None
				)
				lc.purchase_date = doc.posting_date
				lc.purchase_document = doc.name
				lc.purchase_rate = _get_item_rate(doc, item_code)
				lc.supplier = doc.supplier
				lc.supplier_invoice = doc.supplier_delivery_note or ""

				# Append initial lifecycle log
				lc.append("lifecycle_log", {
					"log_timestamp": now_datetime(),
					"from_status": "",
					"to_status": "Received",
					"changed_by": frappe.session.user,
					"company": doc.company,
					"warehouse": lc.current_warehouse,
					"remarks": f"Auto-created from Purchase Receipt {doc.name}",
				})

				lc.flags.ignore_permissions = True
				lc.flags.ignore_validate = True
				lc.insert()
		finally:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lc_lock_key,))

		# Update the IMEI Tracking row with serial_no reference
		row.db_set("serial_no", serial_no, update_modified=False)
		# Real IMEI from custom_track table → stamp authoritative kind + legacy flag
		frappe.db.set_value(
			"Serial No",
			serial_no,
			{"ch_serial_kind": "IMEI", "ch_is_imei": 1},
			update_modified=False,
		)
		created_serials.append(serial_no)

	if created_serials:
		frappe.msgprint(
			_("Created {0} Serial No + Lifecycle records from IMEIs").format(
				len(created_serials)
			),
			indicator="green",
			alert=True,
		)


def on_cancel(doc, method):
	"""Revert CH Serial Lifecycle to blank and deactivate Serial No on PR cancel."""
	if not doc.get("custom_track"):
		return

	for row in doc.custom_track:
		if not row.imei_number:
			continue

		serial_no = row.imei_number.strip()

		# Revert CH Serial Lifecycle
		if frappe.db.exists("CH Serial Lifecycle", serial_no):
			lc = frappe.get_doc("CH Serial Lifecycle", serial_no)
			# Only revert if still in Received state (not further along)
			if lc.lifecycle_status == "Received":
				lc.append("lifecycle_log", {
					"log_timestamp": now_datetime(),
					"from_status": "Received",
					"to_status": "",
					"changed_by": frappe.session.user,
					"company": doc.company,
					"remarks": f"Purchase Receipt {doc.name} cancelled",
				})
				lc.lifecycle_status = ""
				lc.purchase_date = None
				lc.purchase_document = None
				lc.purchase_rate = 0
				lc.supplier = None
				lc.flags.ignore_permissions = True
				lc.flags.ignore_validate = True
				lc.save()


def _get_item_rate(doc, item_code):
	"""Get the purchase rate for an item from the PR items."""
	for item in doc.items:
		if item.item_code == item_code:
			return item.rate or item.base_rate or 0
	return 0


# NOTE: _stamp_imei_flag_on_pr_serials() removed in v3 unify-serial-kind.
# The PR Item.custom_imei (Yes/No) field was an unreliable source — it was
# only set by IMEI tracker import flows, not by the standard Generate button.
# Reading it caused real IMEIs to be silently downgraded to ch_is_imei=0
# when the field defaulted to "No". Stamping is now performed exclusively
# by CustomPurchaseReceipt.on_submit using Item.ch_serial_kind as the
# single source of truth.
