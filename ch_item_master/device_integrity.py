"""Production controls for serialized-device projections.

ERPNext Serial No / Stock Ledger is authoritative for inventory. CH Serial
Lifecycle and CH Customer Device are query-friendly projections only.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint


def _count_and_samples(base_query: str, sample_query: str, sample_limit: int) -> dict:
	count = cint(frappe.db.sql(base_query)[0][0])
	samples = frappe.db.sql(
		f"{sample_query} LIMIT {max(1, min(cint(sample_limit), 100))}",
		as_dict=True,
	) if count else []
	return {"count": count, "samples": samples}


def check_device_integrity(sample_limit: int = 20) -> dict:
	"""Return a read-only source-of-truth audit suitable for CI and monitoring."""
	limit = max(1, min(cint(sample_limit), 100))
	findings = {
		"projection_mutation_permissions": _count_and_samples(
			"""SELECT COUNT(*) FROM (
			SELECT name FROM `tabDocPerm`
			 WHERE parent IN ('CH Customer Device', 'CH Serial Lifecycle')
			   AND (`write` = 1 OR `create` = 1 OR `delete` = 1 OR `import` = 1)
			UNION ALL
			SELECT name FROM `tabCustom DocPerm`
			 WHERE parent IN ('CH Customer Device', 'CH Serial Lifecycle')
			   AND (`write` = 1 OR `create` = 1 OR `delete` = 1 OR `import` = 1)
			) permissions""",
			"""SELECT 'DocPerm' AS source, parent, role, `write`, `create`, `delete`, `import`
			FROM `tabDocPerm`
			WHERE parent IN ('CH Customer Device', 'CH Serial Lifecycle')
			  AND (`write` = 1 OR `create` = 1 OR `delete` = 1 OR `import` = 1)
			UNION ALL
			SELECT 'Custom DocPerm' AS source, parent, role, `write`, `create`, `delete`, `import`
			FROM `tabCustom DocPerm`
			WHERE parent IN ('CH Customer Device', 'CH Serial Lifecycle')
			  AND (`write` = 1 OR `create` = 1 OR `delete` = 1 OR `import` = 1)""",
			limit,
		),
		"serial_without_lifecycle": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabSerial No` sn
			LEFT JOIN `tabCH Serial Lifecycle` lc ON lc.serial_no = sn.name
			WHERE lc.name IS NULL""",
			"""SELECT sn.name AS serial_no, sn.item_code, sn.status, sn.warehouse
			FROM `tabSerial No` sn
			LEFT JOIN `tabCH Serial Lifecycle` lc ON lc.serial_no = sn.name
			WHERE lc.name IS NULL ORDER BY sn.modified DESC""",
			limit,
		),
		"lifecycle_without_serial": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Serial Lifecycle` lc
			LEFT JOIN `tabSerial No` sn ON sn.name = lc.serial_no
			WHERE sn.name IS NULL""",
			"""SELECT lc.name, lc.serial_no, lc.item_code, lc.lifecycle_status
			FROM `tabCH Serial Lifecycle` lc
			LEFT JOIN `tabSerial No` sn ON sn.name = lc.serial_no
			WHERE sn.name IS NULL ORDER BY lc.modified DESC""",
			limit,
		),
		"lifecycle_identity_mismatch": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Serial Lifecycle` lc
			JOIN `tabSerial No` sn ON sn.name = lc.serial_no
			WHERE lc.item_code != sn.item_code""",
			"""SELECT lc.name, lc.serial_no, lc.item_code AS projected_item,
			sn.item_code AS inventory_item FROM `tabCH Serial Lifecycle` lc
			JOIN `tabSerial No` sn ON sn.name = lc.serial_no
			WHERE lc.item_code != sn.item_code
			ORDER BY lc.modified DESC""",
			limit,
		),
		"lifecycle_warehouse_mismatch": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Serial Lifecycle` lc
			JOIN `tabSerial No` sn ON sn.name = lc.serial_no
			WHERE COALESCE(lc.lifecycle_status, '') IN ('', 'Received', 'In Stock', 'Displayed', 'Refurbished', 'Repaired')
			AND COALESCE(lc.current_warehouse, '') != COALESCE(sn.warehouse, '')""",
			"""SELECT lc.serial_no, lc.lifecycle_status, lc.current_warehouse AS projected_warehouse,
			sn.warehouse AS inventory_warehouse FROM `tabCH Serial Lifecycle` lc
			JOIN `tabSerial No` sn ON sn.name = lc.serial_no
			WHERE COALESCE(lc.lifecycle_status, '') IN ('', 'Received', 'In Stock', 'Displayed', 'Refurbished', 'Repaired')
			AND COALESCE(lc.current_warehouse, '') != COALESCE(sn.warehouse, '')
			ORDER BY lc.modified DESC""",
			limit,
		),
		"invalid_inventory_device": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Customer Device` d
			LEFT JOIN `tabSerial No` sn ON sn.name = d.inventory_serial
			WHERE d.device_source = 'Inventory Serial' AND (
				COALESCE(d.inventory_serial, '') = '' OR d.inventory_serial != d.serial_no
				OR sn.name IS NULL OR d.item_code != sn.item_code
			)""",
			"""SELECT d.name, d.serial_no, d.inventory_serial, d.item_code AS projected_item,
			sn.item_code AS inventory_item FROM `tabCH Customer Device` d
			LEFT JOIN `tabSerial No` sn ON sn.name = d.inventory_serial
			WHERE d.device_source = 'Inventory Serial' AND (
				COALESCE(d.inventory_serial, '') = '' OR d.inventory_serial != d.serial_no
				OR sn.name IS NULL OR d.item_code != sn.item_code
			) ORDER BY d.modified DESC""",
			limit,
		),
		"external_inventory_collision": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Customer Device` d
			LEFT JOIN `tabSerial No` sn ON sn.name = d.serial_no
			WHERE d.device_source = 'Customer Provided' AND (
				COALESCE(d.inventory_serial, '') != '' OR COALESCE(d.lifecycle, '') != '' OR sn.name IS NOT NULL
			)""",
			"""SELECT d.name, d.serial_no, d.inventory_serial, d.lifecycle, sn.status
			FROM `tabCH Customer Device` d
			LEFT JOIN `tabSerial No` sn ON sn.name = d.serial_no
			WHERE d.device_source = 'Customer Provided' AND (
				COALESCE(d.inventory_serial, '') != '' OR COALESCE(d.lifecycle, '') != '' OR sn.name IS NOT NULL
			) ORDER BY d.modified DESC""",
			limit,
		),
		"verified_owner_still_in_stock": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Customer Device` d
			JOIN `tabSerial No` sn ON sn.name = d.inventory_serial
			WHERE d.device_source = 'Inventory Serial'
			AND d.ownership_verification = 'Verified'
			AND d.current_status IN ('Owned', 'Sold') AND sn.status = 'Active'""",
			"""SELECT d.name, d.customer, d.serial_no, d.current_status, sn.warehouse
			FROM `tabCH Customer Device` d
			JOIN `tabSerial No` sn ON sn.name = d.inventory_serial
			WHERE d.device_source = 'Inventory Serial'
			AND d.ownership_verification = 'Verified'
			AND d.current_status IN ('Owned', 'Sold') AND sn.status = 'Active'
			ORDER BY d.modified DESC""",
			limit,
		),
		"submitted_external_plan_projection_gap": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabActive VAS Plans` p
			LEFT JOIN `tabCH Customer Device` d ON d.serial_no = p.serial_no
			WHERE p.docstatus = 1 AND p.is_external_device = 1 AND (
				d.name IS NULL OR d.device_source != 'Customer Provided' OR d.customer != p.customer
			)""",
			"""SELECT p.name AS active_plan, p.customer, p.serial_no, d.name AS customer_device,
			d.device_source FROM `tabActive VAS Plans` p
			LEFT JOIN `tabCH Customer Device` d ON d.serial_no = p.serial_no
			WHERE p.docstatus = 1 AND p.is_external_device = 1 AND (
				d.name IS NULL OR d.device_source != 'Customer Provided' OR d.customer != p.customer
			) ORDER BY p.modified DESC""",
			limit,
		),
		"submitted_inventory_plan_projection_gap": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabActive VAS Plans` p
			LEFT JOIN `tabCH Customer Device` d ON d.serial_no = p.serial_no
			WHERE p.docstatus = 1 AND p.is_external_device = 0
			  AND COALESCE(p.serial_no, '') != '' AND (
				d.name IS NULL OR d.device_source != 'Inventory Serial'
				OR d.inventory_serial != p.serial_no OR d.customer != p.customer
			)""",
			"""SELECT p.name AS active_plan, p.customer, p.serial_no, d.name AS customer_device,
			d.device_source, d.inventory_serial FROM `tabActive VAS Plans` p
			LEFT JOIN `tabCH Customer Device` d ON d.serial_no = p.serial_no
			WHERE p.docstatus = 1 AND p.is_external_device = 0
			  AND COALESCE(p.serial_no, '') != '' AND (
				d.name IS NULL OR d.device_source != 'Inventory Serial'
				OR d.inventory_serial != p.serial_no OR d.customer != p.customer
			) ORDER BY p.modified DESC""",
			limit,
		),
		"return_projection_misclassified": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Customer Device` d
			LEFT JOIN `tabSales Invoice` si ON si.name = d.purchase_invoice
			LEFT JOIN `tabSerial No` sn ON sn.name = d.inventory_serial
			WHERE (si.docstatus = 1 AND si.is_return = 1)
			   OR (d.device_source = 'Inventory Serial' AND d.current_status = 'Returned'
			       AND COALESCE(sn.status, '') != 'Active')""",
			"""SELECT d.name, d.serial_no, d.current_status, d.purchase_invoice,
			si.is_return, sn.status AS inventory_status FROM `tabCH Customer Device` d
			LEFT JOIN `tabSales Invoice` si ON si.name = d.purchase_invoice
			LEFT JOIN `tabSerial No` sn ON sn.name = d.inventory_serial
			WHERE (si.docstatus = 1 AND si.is_return = 1)
			   OR (d.device_source = 'Inventory Serial' AND d.current_status = 'Returned'
			       AND COALESCE(sn.status, '') != 'Active')
			ORDER BY d.modified DESC""",
			limit,
		),
		"legacy_unverified": _count_and_samples(
			"""SELECT COUNT(*) FROM `tabCH Customer Device`
			WHERE device_source = 'Legacy Unverified' OR ownership_verification = 'Legacy Unverified'""",
			"""SELECT name, customer, serial_no, device_source, ownership_verification,
			verification_notes FROM `tabCH Customer Device`
			WHERE device_source = 'Legacy Unverified' OR ownership_verification = 'Legacy Unverified'
			ORDER BY modified DESC""",
			limit,
		),
	}
	total_anomalies = sum(row["count"] for row in findings.values())
	return {
		"ok": total_anomalies == 0,
		"total_anomalies": total_anomalies,
		"authority": {
			"inventory_identity_and_location": "Serial No / Stock Ledger Entry",
			"inventory_history_projection": "CH Serial Lifecycle",
			"customer_asset_projection": "CH Customer Device",
			"external_devices_enter_inventory": False,
		},
		"findings": findings,
	}


def monitor_device_integrity() -> dict:
	"""Daily production monitor; healthy runs remain silent."""
	report = check_device_integrity(sample_limit=10)
	if not report["ok"]:
		frappe.log_error(
			title=_("Serialized Device Integrity Drift"),
			message=json.dumps(report, indent=2, default=str),
		)
	return report


def get_dependency_free_legacy_devices() -> list[dict]:
	"""Return quarantined rows with no business document supporting them."""
	return frappe.db.sql(
		"""
		SELECT d.name, d.customer, d.serial_no, d.device_source,
		       d.ownership_verification, d.verification_notes
		  FROM `tabCH Customer Device` d
		 WHERE (d.device_source = 'Legacy Unverified'
		        OR d.ownership_verification = 'Legacy Unverified')
		   AND d.current_status = 'Unverified'
		   AND NOT EXISTS (
		       SELECT 1 FROM `tabActive VAS Plans` p
		        WHERE p.serial_no = d.serial_no
		   )
		   AND NOT EXISTS (
		       SELECT 1 FROM `tabCH Customer Device VAS` v
		        WHERE v.parent = d.name
		   )
		   AND NOT EXISTS (
		       SELECT 1 FROM `tabCH Warranty Claim` c
		        WHERE c.serial_no = d.serial_no
		   )
		   AND NOT EXISTS (
		       SELECT 1 FROM `tabSales Invoice` si
		        WHERE si.name = d.purchase_invoice
		   )
		 ORDER BY d.modified, d.name
		""",
		as_dict=True,
	)


def purge_dependency_free_legacy_devices(confirm: int = 0) -> dict:
	"""CLI-only cleanup for reviewed test/legacy data; never scheduled.

	Rows with a VAS plan, claim, child record, or valid invoice are excluded.
	Frappe's link checks remain enabled so an unknown dependency aborts rather
	than cascading silently.
	"""
	if not cint(confirm):
		return {"deleted": 0, "dry_run": True, "candidates": get_dependency_free_legacy_devices()}
	if frappe.session.user != "Administrator":
		frappe.throw(_("Administrator is required for legacy device cleanup."), frappe.PermissionError)

	candidates = get_dependency_free_legacy_devices()
	deleted = []
	for row in candidates:
		frappe.delete_doc(
			"CH Customer Device",
			row.name,
			ignore_permissions=True,
			force=False,
		)
		deleted.append(row.name)
	frappe.db.commit()
	return {"deleted": len(deleted), "dry_run": False, "names": deleted}
