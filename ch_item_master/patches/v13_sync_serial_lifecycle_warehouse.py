"""
v13: One-shot backfill — sync CH Serial Lifecycle.current_warehouse with
the live Serial No.warehouse for serials whose lifecycle was pinned to
the original warehouse, and auto-create missing lifecycle rows for any
serials that appear in stock but have no lifecycle record.

This patch fixes the historical drift that built up before the
`Serial No: on_update` hook (added in the same release) started
maintaining the link automatically.

Idempotent — safe to re-run.
"""

import frappe


_WAREHOUSE_TRACKING_STATUSES = (
	"",
	"Received",
	"In Stock",
	"Displayed",
	"Refurbished",
	"Repaired",
)


def execute():
	synced = _sync_warehouse_for_existing()
	created = _create_missing_lifecycle_rows()
	print(
		f"v13_sync_serial_lifecycle_warehouse: synced {synced} rows, "
		f"created {created} missing lifecycle rows"
	)


def _sync_warehouse_for_existing() -> int:
	rows = frappe.db.sql(
		"""
		SELECT sn.name             AS serial_no,
		       sn.warehouse        AS sn_warehouse,
		       lc.current_warehouse AS lc_warehouse,
		       lc.lifecycle_status  AS lc_status,
		       lc.current_company   AS lc_company,
		       wh.company           AS wh_company
		FROM `tabSerial No` sn
		INNER JOIN `tabCH Serial Lifecycle` lc ON lc.name = sn.name
		LEFT JOIN `tabWarehouse` wh ON wh.name = sn.warehouse
		WHERE sn.warehouse IS NOT NULL AND sn.warehouse <> ''
		  AND (sn.warehouse <> IFNULL(lc.current_warehouse, '')
		       OR IFNULL(wh.company, '') <> IFNULL(lc.current_company, ''))
		""",
		as_dict=True,
	)
	fixed = 0
	for r in rows:
		status = r.lc_status or ""
		if status not in _WAREHOUSE_TRACKING_STATUSES:
			continue
		updates = {}
		if r.sn_warehouse and r.sn_warehouse != r.lc_warehouse:
			updates["current_warehouse"] = r.sn_warehouse
		if r.wh_company and r.wh_company != r.lc_company:
			updates["current_company"] = r.wh_company
		if status == "Received" and r.sn_warehouse:
			updates["lifecycle_status"] = "In Stock"
		if updates:
			frappe.db.set_value("CH Serial Lifecycle", r.serial_no, updates)
			fixed += 1
	return fixed


def _create_missing_lifecycle_rows() -> int:
	rows = frappe.db.sql(
		"""
		SELECT sn.name      AS serial_no,
		       sn.item_code AS item_code,
		       sn.warehouse AS warehouse,
		       wh.company   AS warehouse_company
		FROM `tabSerial No` sn
		LEFT JOIN `tabCH Serial Lifecycle` lc ON lc.name = sn.name
		LEFT JOIN `tabWarehouse` wh ON wh.name = sn.warehouse
		WHERE lc.name IS NULL
		""",
		as_dict=True,
	)
	created = 0
	for r in rows:
		if frappe.db.exists("CH Serial Lifecycle", r.serial_no):
			continue
		lc = frappe.new_doc("CH Serial Lifecycle")
		lc.serial_no = r.serial_no
		lc.item_code = r.item_code
		lc.current_warehouse = r.warehouse
		lc.current_company = r.warehouse_company
		lc.lifecycle_status = "In Stock" if r.warehouse else "Received"
		cleaned = str(r.serial_no).strip().replace(" ", "").replace("-", "")
		if cleaned.isdigit() and len(cleaned) == 15:
			lc.imei_number = cleaned
		lc.flags.ignore_permissions = True
		lc.flags.ignore_validate = True
		try:
			lc.insert()
			created += 1
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"v13 backfill: lifecycle create failed for {r.serial_no}",
			)
	return created
