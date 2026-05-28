"""
Serial No → CH Serial Lifecycle + CH Stock Bin synchronization.

`Serial No` (ERPNext core) is the **authoritative** source of truth for a
device's current warehouse and status. Two derived projections must stay
consistent with it:

  1. CH Serial Lifecycle  — IMEI Tracker dashboard view (warehouse, status,
                            company, store, lifecycle_log).
  2. CH Stock Bin         — Virtual bin overlay (Sellable / Damaged /
                            Disposed / Reserved / Transfer).

This module reacts to every Serial No.on_update and:

  • Mirrors `warehouse` → `lifecycle.current_warehouse` (warehouse-tracking
    statuses only — Sold/Returned/etc. are owned by the business flow).
  • Maps `Serial No.status` transitions onto `lifecycle_status`
    (Delivered → Sold; Inactive → Scrapped).
  • Deletes any CH Stock Bin record when the device leaves the warehouse
    (warehouse becomes NULL because it was sold/delivered/transferred-out
    or status becomes Delivered/Inactive). A bin can only exist for a
    device that is physically present in a warehouse.

Without this synchronization a single IMEI could end up reported in three
mutually-inconsistent states across the three tables — see fix notes for
serial 356002001000002 (was: Delivered in Serial No, In Stock in Lifecycle,
Damaged in Stock Bin, with zero SLEs).
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


# Map Serial No.status (ERPNext core) → lifecycle_status (CH Serial
# Lifecycle). Only auto-apply when current lifecycle_status is in the
# "from" set — anything else is owned by an explicit business flow
# (Warranty Claim, Buyback Order, Returned/In Service workflow) and
# must not be auto-overwritten by stock movements.
_SN_STATUS_TO_LIFECYCLE = {
	# When ERPNext flips Serial No → Delivered (DN/SI with update_stock=1),
	# promote In Stock/Displayed/Repaired/Refurbished → Sold.
	"Delivered": {
		"from": {"", "Received", "In Stock", "Displayed", "Repaired", "Refurbished"},
		"to": "Sold",
	},
	# When Serial No → Inactive (write-off / scrap), terminate lifecycle.
	"Inactive": {
		"from": {"", "Received", "In Stock", "Displayed", "Repaired", "Refurbished",
				 "Returned", "Buyback", "In Service"},
		"to": "Scrapped",
	},
	# "Expired" is a warranty-expiry flag, not a stock state — no-op.
	# "Active" reactivation is intentionally NOT auto-mapped: the route
	# back into stock (return/refurbish/buyback) is business-owned.
}


def on_update(doc, method=None):
	"""Mirror Serial No state onto CH Serial Lifecycle + CH Stock Bin.

	Authoritative source = ERPNext Serial No. Everything else is a
	derived projection that must agree with it.
	"""
	serial_no = doc.name
	sn_warehouse = doc.warehouse or None
	sn_status = (doc.status or "").strip()
	company = None
	if sn_warehouse:
		company = frappe.db.get_value("Warehouse", sn_warehouse, "company")

	# ── Bin cleanup ──────────────────────────────────────────────────────
	# A CH Stock Bin record only makes sense for a device physically
	# present in a warehouse. The moment ERPNext clears `warehouse`
	# (delivered/transferred-out) or marks the serial Delivered/Inactive,
	# the bin overlay must be removed — otherwise the same IMEI can be
	# simultaneously reported as Sold AND sitting in a Damaged bin.
	if (not sn_warehouse) or sn_status in ("Delivered", "Inactive"):
		_remove_stock_bin_if_any(serial_no, reason=f"Serial No.status={sn_status or 'NULL'}, warehouse={sn_warehouse or 'NULL'}")

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

	status = lc_row.lifecycle_status or ""
	updates = {}

	# ── Status sync (Serial No.status → lifecycle_status) ────────────────
	mapping = _SN_STATUS_TO_LIFECYCLE.get(sn_status)
	if mapping and status in mapping["from"] and status != mapping["to"]:
		updates["lifecycle_status"] = mapping["to"]
		# When a sale terminates In Stock, also unset the warehouse
		# projection — the device is no longer "in" a warehouse.
		if mapping["to"] in ("Sold", "Scrapped"):
			updates["current_warehouse"] = None

	# ── Warehouse sync ───────────────────────────────────────────────────
	# Only mirror warehouse when we are in (or just entered) a tracking
	# status. Re-evaluate using the post-update lifecycle_status if we
	# just changed it above.
	effective_lc = updates.get("lifecycle_status", status)
	if effective_lc in _WAREHOUSE_TRACKING_STATUSES:
		if sn_warehouse and lc_row.current_warehouse != sn_warehouse:
			updates["current_warehouse"] = sn_warehouse
		if company and lc_row.current_company != company:
			updates["current_company"] = company

		# Keep current_store in sync with the warehouse so the IMEI Tracker
		# shows the correct store after an inter-store transfer.
		if sn_warehouse and sn_warehouse != lc_row.current_warehouse:
			store_name = frappe.db.get_value("CH Store", {"warehouse": sn_warehouse}, "name")
			if store_name and store_name != lc_row.get("current_store"):
				updates["current_store"] = store_name

		# When stock arrives in a warehouse and the lifecycle is still in the
		# initial "Received" state, promote it to "In Stock" so it appears in
		# the dashboard's In-Stock bucket.
		if sn_warehouse and effective_lc == "Received":
			updates["lifecycle_status"] = "In Stock"

	if not updates:
		return

	# Lightweight DB update — bypass full validation (we are reacting to an
	# already-validated stock movement). modified is bumped automatically.
	frappe.db.set_value("CH Serial Lifecycle", lc_row.name, updates)


def _remove_stock_bin_if_any(serial_no, reason=""):
	"""Drop the CH Stock Bin overlay row for a serial that has left the
	warehouse (sold / scrapped / transferred-out). Logs the removal."""
	rec = frappe.db.get_value("CH Stock Bin", {"serial_no": serial_no}, "name")
	if not rec:
		return
	from_bin = frappe.db.get_value("CH Stock Bin", rec, "bin_type") or "Sellable"
	wh = frappe.db.get_value("CH Stock Bin", rec, "warehouse") or ""
	item_code = frappe.db.get_value("CH Stock Bin", rec, "item_code") or ""
	try:
		from frappe.utils import now_datetime
		frappe.get_doc({
			"doctype": "CH Stock Bin Log",
			"serial_no": serial_no,
			"item_code": item_code,
			"warehouse": wh,
			"from_bin": from_bin,
			"to_bin": "Sellable",  # logical exit
			"moved_at": now_datetime(),
			"moved_by": frappe.session.user or "Administrator",
			"reason": f"Auto-cleared by Serial No sync — {reason}",
		}).insert(ignore_permissions=True)
		frappe.delete_doc("CH Stock Bin", rec, ignore_permissions=True, force=True)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"CH Stock Bin auto-clear failed for {serial_no}",
		)


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

		# Mirror per-serial classification (SAP MARC-SERNP equivalent).
		# Prefer the Serial No's own ch_serial_kind (stamped at PR time);
		# fall back to Item.ch_serial_kind so externally-imported serials
		# still resolve a kind.
		sn_kind = (getattr(sn_doc, "ch_serial_kind", None) or "").strip()
		if sn_kind not in ("IMEI", "Barcode", "UOM") and sn_doc.item_code:
			sn_kind = (frappe.db.get_value(
				"Item", sn_doc.item_code, "ch_serial_kind"
			) or "").strip()
		if sn_kind in ("IMEI", "Barcode", "UOM"):
			lc.ch_serial_kind = sn_kind

		# Resolve warehouse → CH Store so the IMEI Tracker shows the correct store
		if warehouse:
			store_name = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")
			if store_name:
				lc.current_store = store_name

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
