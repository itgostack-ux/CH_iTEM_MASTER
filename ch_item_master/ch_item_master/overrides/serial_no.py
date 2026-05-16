"""
Serial No → CH Serial Lifecycle synchronization.

Whenever ERPNext updates a Serial No (typically because a Stock Entry,
Delivery Note, Sales Invoice, POS Invoice, or Stock Reconciliation has
moved the serial), mirror the new warehouse onto CH Serial Lifecycle so
the IMEI Tracker dashboard reflects the current location.

If a CH Serial Lifecycle row does not yet exist for the serial (e.g. the
serial was introduced via a direct Stock Entry rather than via a Purchase
Receipt with custom_imei_track), auto-create a minimal row so the device
is still visible in the tracker.

This is the missing link that explained the "IMEI Tracker shows zero KPIs
even though Stock Entry created SLEs" bug: ERPNext core updates
`tabSerial No.warehouse` on every stock movement, but nothing was
propagating that change to `tabCH Serial Lifecycle.current_warehouse`.
"""

import frappe


# Statuses where the device is "sitting in a warehouse" — safe to
# overwrite location for. Any other status (Sold, Returned, In Service,
# Buyback, Scrapped, Lost) is owned by its respective business flow and
# the warehouse should not be silently changed by a stock movement.
_WAREHOUSE_TRACKING_STATUSES = {
	"",
	"Received",
	"In Stock",
	"Displayed",
	"Refurbished",
	"Repaired",
}


def on_update(doc, method=None):
	"""Mirror Serial No.warehouse onto CH Serial Lifecycle.current_warehouse."""
	serial_no = doc.name
	sn_warehouse = doc.warehouse or None
	company = None
	if sn_warehouse:
		company = frappe.db.get_value("Warehouse", sn_warehouse, "company")

	lc_row = frappe.db.get_value(
		"CH Serial Lifecycle",
		serial_no,
		["name", "current_warehouse", "lifecycle_status", "current_company"],
		as_dict=True,
	)

	if not lc_row:
		# Auto-create minimal lifecycle row so the serial is visible in the
		# IMEI Tracker even if it wasn't introduced via Purchase Receipt.
		_create_minimal_lifecycle(doc, sn_warehouse, company)
		return

	# Only sync warehouse if the device is in a warehouse-tracking status.
	status = lc_row.lifecycle_status or ""
	if status not in _WAREHOUSE_TRACKING_STATUSES:
		return

	updates = {}
	if sn_warehouse and lc_row.current_warehouse != sn_warehouse:
		updates["current_warehouse"] = sn_warehouse
	if company and lc_row.current_company != company:
		updates["current_company"] = company

	# When stock arrives in a warehouse and the lifecycle is still in the
	# initial "Received" state, promote it to "In Stock" so it appears in
	# the dashboard's In-Stock bucket.
	if sn_warehouse and status == "Received":
		updates["lifecycle_status"] = "In Stock"

	if not updates:
		return

	# Lightweight DB update — bypass full validation (we are reacting to an
	# already-validated stock movement). modified is bumped automatically.
	frappe.db.set_value("CH Serial Lifecycle", lc_row.name, updates)


def _create_minimal_lifecycle(sn_doc, warehouse, company):
	"""Create a minimal CH Serial Lifecycle row for a previously-unseen serial."""
	lock_key = f"serial_create_{frappe.scrub(str(sn_doc.name))}_lifecycle"
	got_lock = frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_key,))[0][0]
	if not got_lock:
		frappe.log_error(
			f"Could not acquire lifecycle lock for serial {sn_doc.name}",
			"CH Serial Lifecycle Auto-Create Lock Timeout",
		)
		return
	try:
		if frappe.db.exists("CH Serial Lifecycle", sn_doc.name):
			return  # raced with another worker — nothing to do

		lc = frappe.new_doc("CH Serial Lifecycle")
		lc.serial_no = sn_doc.name
		lc.item_code = sn_doc.item_code
		lc.current_warehouse = warehouse
		lc.current_company = company
		lc.lifecycle_status = "In Stock" if warehouse else "Received"

		# Optional IMEI auto-detect — exactly 15 digits
		cleaned = str(sn_doc.name).strip().replace(" ", "").replace("-", "")
		if cleaned.isdigit() and len(cleaned) == 15:
			lc.imei_number = cleaned

		lc.flags.ignore_permissions = True
		lc.flags.ignore_validate = True
		lc.insert()
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"CH Serial Lifecycle auto-create failed for {sn_doc.name}",
		)
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))
