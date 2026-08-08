"""Backfill explicit device provenance and repair projection drift."""

import frappe


def execute():
	# Inventory-backed customer assets: Serial No owns identity and item.
	frappe.db.sql(
		"""
		UPDATE `tabCH Customer Device` d
		JOIN `tabSerial No` sn ON sn.name = d.serial_no
		LEFT JOIN `tabCH Serial Lifecycle` lc ON lc.serial_no = sn.name
		   SET d.device_source = 'Inventory Serial',
		       d.inventory_serial = sn.name,
		       d.ownership_verification = 'Verified',
		       d.item_code = sn.item_code,
		       d.imei_number = sn.name,
		       d.lifecycle = lc.name
		"""
	)

	# Return invoices previously ran the sale-registration hook and created an
	# Owned projection from a negative line. Restore the original sale reference
	# and mark the device Returned; stock itself is already correctly back in SN.
	frappe.db.sql(
		"""
		UPDATE `tabCH Customer Device` d
		JOIN `tabSales Invoice` ret
		  ON ret.name = d.purchase_invoice
		 AND ret.docstatus = 1
		 AND ret.is_return = 1
		LEFT JOIN `tabSales Invoice` original ON original.name = ret.return_against
		   SET d.current_status = 'Returned',
		       d.purchase_invoice = COALESCE(NULLIF(ret.return_against, ''), d.purchase_invoice),
		       d.purchase_date = COALESCE(original.posting_date, d.purchase_date),
		       d.ownership_verification = 'Verified',
		       d.verification_notes = CONCAT('Returned via ', ret.name)
		"""
	)

	# Customer-provided devices are customer assets only. They intentionally
	# have no Serial No, warehouse, lifecycle row or stock ledger impact.
	frappe.db.sql(
		"""
		UPDATE `tabCH Customer Device` d
		JOIN `tabActive VAS Plans` p
		  ON p.serial_no = d.serial_no
		 AND p.is_external_device = 1
		 AND p.docstatus = 1
		LEFT JOIN `tabSerial No` sn ON sn.name = d.serial_no
		   SET d.device_source = 'Customer Provided',
		       d.inventory_serial = NULL,
		       d.ownership_verification = 'Verified',
		       d.current_status = 'Owned',
		       d.lifecycle = NULL,
		       d.imei_number = d.serial_no,
		       d.verification_notes = CONCAT('Customer-provided device; covered by ', p.name)
		 WHERE sn.name IS NULL
		"""
	)

	# Preserve unsupported historical rows, but stop presenting them as proven
	# customer ownership. Operators may resolve or purge them after review.
	frappe.db.sql(
		"""
		UPDATE `tabCH Customer Device` d
		LEFT JOIN `tabSerial No` sn ON sn.name = d.serial_no
		   SET d.device_source = 'Legacy Unverified',
		       d.inventory_serial = NULL,
		       d.ownership_verification = 'Legacy Unverified',
		       d.current_status = 'Unverified',
		       d.lifecycle = NULL,
		       d.verification_notes = 'Quarantined: no inventory serial or verified external-device plan'
		 WHERE sn.name IS NULL
		   AND COALESCE(d.device_source, '') != 'Customer Provided'
		"""
	)

	# An Active warehouse serial cannot simultaneously be a verified Owned/Sold
	# asset. Quarantine the ownership claim; never fabricate a stock movement.
	frappe.db.sql(
		"""
		UPDATE `tabCH Customer Device` d
		JOIN `tabSerial No` sn ON sn.name = d.inventory_serial
		   SET d.ownership_verification = 'Legacy Unverified',
		       d.current_status = 'Unverified',
		       d.verification_notes = 'Quarantined: ownership has no submitted sale that removed the serial from stock'
		 WHERE sn.status = 'Active'
		   AND d.current_status IN ('Owned', 'Sold')
		"""
	)

	# Converge every standard serial into the lifecycle projection. The helper
	# is idempotent and only creates the currently missing one-to-one row.
	missing = frappe.db.sql_list(
		"""
		SELECT sn.name
		  FROM `tabSerial No` sn
		LEFT JOIN `tabCH Serial Lifecycle` lc ON lc.serial_no = sn.name
		 WHERE lc.name IS NULL
		"""
	)
	if missing:
		from ch_item_master.ch_item_master.overrides.serial_no import sync_serials_to_lifecycle

		sync_serials_to_lifecycle(missing)

	frappe.clear_cache(doctype="CH Customer Device")
	frappe.clear_cache(doctype="CH Serial Lifecycle")
