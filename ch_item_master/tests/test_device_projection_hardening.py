"""Non-destructive E2E for serialized-device source boundaries.

Run:
  bench --site erpnext.local execute ch_item_master.tests.test_device_projection_hardening.run
"""

import frappe

from ch_item_master.ch_customer_master.doctype.ch_customer_device.ch_customer_device import (
	CHCustomerDevice,
)
from ch_item_master.device_integrity import check_device_integrity


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


def run():
	frappe.set_user("Administrator")
	savepoint = "device_projection_hardening_e2e"
	frappe.db.savepoint(savepoint)
	checks = []
	try:
		meta = frappe.get_meta("CH Customer Device")
		_assert(not meta.allow_import, "CH Customer Device must not allow direct import")
		for permission in meta.permissions:
			_assert(
				not permission.get("create") and not permission.get("write")
				and not permission.get("delete") and not permission.get("import"),
				f"Direct mutation permission remains for {permission.role}",
			)
		checks.append("customer-device projection is read-only to roles/imports")

		lifecycle_meta = frappe.get_meta("CH Serial Lifecycle")
		_assert(not lifecycle_meta.allow_import, "CH Serial Lifecycle must not allow direct import")
		for permission in lifecycle_meta.permissions:
			_assert(
				not permission.get("create") and not permission.get("write")
				and not permission.get("delete") and not permission.get("import"),
				f"Direct lifecycle mutation permission remains for {permission.role}",
			)
		checks.append("serial lifecycle projection is read-only to roles/imports")

		customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		item = (
			frappe.db.get_value("Item", "EXTERNAL-DEVICE", "name")
			or frappe.db.get_value("Item", {"disabled": 0}, "name")
		)
		_assert(customer and item, "An active customer and item are required")
		external_identifier = f"EXT-E2E-{frappe.generate_hash(length=12).upper()}"
		external = CHCustomerDevice.create_or_update_external(
			external_identifier,
			customer,
			item,
			verification_notes="Device projection hardening E2E",
		)
		_assert(external.device_source == "Customer Provided", "External source was not explicit")
		_assert(not external.inventory_serial and not external.lifecycle, "External device entered inventory projection")
		_assert(not frappe.db.exists("Serial No", external_identifier), "External device created Serial No")
		_assert(not frappe.db.exists("CH Serial Lifecycle", external_identifier), "External device created lifecycle")
		checks.append("customer-provided device remains outside inventory and lifecycle")

		active_serial = frappe.db.get_value("Serial No", {"status": "Active"}, "name")
		_assert(active_serial, "An active inventory serial is required")
		collision_blocked = False
		try:
			CHCustomerDevice.create_or_update_external(active_serial, customer, item)
		except frappe.ValidationError:
			collision_blocked = True
		_assert(collision_blocked, "Inventory serial was accepted as customer-provided")
		checks.append("external/inventory identity collision is blocked")

		ownership_blocked = False
		serial_item = frappe.db.get_value("Serial No", active_serial, "item_code")
		probe = frappe.new_doc("CH Customer Device")
		probe.update({
			"customer": customer,
			"serial_no": active_serial,
			"device_source": "Inventory Serial",
			"inventory_serial": active_serial,
			"ownership_verification": "Verified",
			"item_code": serial_item,
			"current_status": "Owned",
		})
		try:
			probe.validate_device_source()
		except frappe.ValidationError:
			ownership_blocked = True
		_assert(ownership_blocked, "Active warehouse stock was accepted as verified customer ownership")
		checks.append("stock/customer ownership split-brain is blocked")

		fake_lifecycle = frappe.new_doc("CH Serial Lifecycle")
		fake_lifecycle.serial_no = f"MISSING-{frappe.generate_hash(length=10)}"
		fake_lifecycle.item_code = item
		missing_serial_blocked = False
		try:
			fake_lifecycle._sync_inventory_identity()
		except frappe.ValidationError:
			missing_serial_blocked = True
		_assert(missing_serial_blocked, "Lifecycle row without Serial No was accepted")
		checks.append("orphan lifecycle creation is blocked")

		special_serial = frappe.db.sql(
			"""SELECT name FROM `tabSerial No`
			WHERE name LIKE '%%<%%' OR name LIKE '%%>%%' LIMIT 1"""
		)
		if special_serial:
			special_serial = special_serial[0][0]
			lifecycle_name = frappe.db.get_value(
				"CH Serial Lifecycle", {"serial_no": special_serial}, "name"
			)
			_assert(lifecycle_name, "Special-character serial has no lifecycle before recreation test")
			frappe.delete_doc(
				"CH Serial Lifecycle", lifecycle_name, ignore_permissions=True
			)
			from ch_item_master.ch_item_master.overrides.serial_no import sync_serials_to_lifecycle

			sync_serials_to_lifecycle([special_serial])
			recreated = frappe.db.get_value(
				"CH Serial Lifecycle", {"serial_no": special_serial}, ["name", "serial_no"], as_dict=True
			)
			_assert(recreated and recreated.serial_no == special_serial, "Exact special serial link was not preserved")
			_assert("<" not in recreated.name and ">" not in recreated.name, "Unsafe lifecycle name was generated")
			checks.append("legacy special-character serial recreates with a safe projection name")

		report = check_device_integrity(sample_limit=5)
		_assert(report["ok"], f"Integrity monitor found drift: {report}")
		checks.append("production integrity monitor reports zero drift")

		return {"passed": len(checks), "checks": checks, "integrity": report}
	finally:
		frappe.db.rollback(save_point=savepoint)
