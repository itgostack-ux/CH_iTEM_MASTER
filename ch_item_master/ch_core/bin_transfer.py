"""Bin transfer / stock allocation engine for CH Store warehouses.

Every CH Store has exactly 5 stock-state bins (see ch_store.STORE_BIN_TYPES):
	Sellable, In-Transit, Damaged, Disposed, Reserved

This module is the single source of truth for:
 1. Bin lookup helpers (store + bin_type -> warehouse name)
 2. Default allocation rules per business event
	(sale, return, damage report, pre-booking, dispose, etc.)
 3. The bin-to-bin transfer engine (creates a single Stock Entry of
	type 'Material Transfer' with a reason link)
 4. POS-facing whitelisted APIs

It deliberately wraps standard ERPNext Stock Entry instead of inventing a
parallel ledger, so every move is auditable in the standard Stock Ledger
and contributes to existing reports.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


# ──────────────────────────────────────────────────────────────────────────
# 1. Bin types and default allocation rules
# ──────────────────────────────────────────────────────────────────────────

# Canonical list of bin types (mirrors ch_store.STORE_BIN_TYPES).
BIN_TYPES = ("Sellable", "In-Transit", "Damaged", "Disposed", "Reserved")

# Default rules: for each business event, where do items move?
# Format: { event: (from_bin_type, to_bin_type) }
# `None` means "decided at runtime" (e.g. POS user picks).
DEFAULT_ALLOCATION_RULES = {
	# Outbound to customer — always debits Sellable.
	"pos_sale": ("Sellable", None),               # leaves the store entirely
	"sales_return": (None, "Sellable"),           # back into Sellable by default
	# Internal movements.
	"pre_book": ("Sellable", "Reserved"),         # holds stock for an order
	"release_reservation": ("Reserved", "Sellable"),
	"mark_damaged": ("Sellable", "Damaged"),
	"damaged_in_transit": ("In-Transit", "Damaged"),
	"received_from_zone": ("In-Transit", "Sellable"),  # inbound completion
	"dispatch_to_zone": ("Sellable", "In-Transit"),    # outbound start
	"dispose": ("Damaged", "Disposed"),
	"return_to_sellable": ("Damaged", "Sellable"),     # repaired / re-graded
}


# ──────────────────────────────────────────────────────────────────────────
# 2. Bin lookup helpers
# ──────────────────────────────────────────────────────────────────────────

def get_store_bin(store: str, bin_type: str) -> str:
	"""Return warehouse name for the given store + bin_type. Throws if missing."""
	if not store or not bin_type:
		frappe.throw(_("Store and bin type are required."))

	if bin_type not in BIN_TYPES:
		frappe.throw(
			_("Invalid bin type {0}. Must be one of: {1}").format(bin_type, ", ".join(BIN_TYPES))
		)

	wh = frappe.db.get_value(
		"Warehouse",
		{"ch_store": store, "ch_bin_type": bin_type, "disabled": 0},
		"name",
	)
	if not wh:
		frappe.throw(
			_("Store {0} has no {1} bin configured. Re-run bench migrate or open the store and save it again.").format(
				frappe.bold(store), frappe.bold(bin_type)
			),
			title=_("Bin Missing"),
		)
	return wh


def get_store_bins(store: str) -> dict:
	"""Return {bin_type: warehouse_name} for all bins of the store."""
	rows = frappe.get_all(
		"Warehouse",
		filters={"ch_store": store, "ch_bin_type": ["!=", ""], "disabled": 0},
		fields=["ch_bin_type", "name"],
	)
	return {r.ch_bin_type: r.name for r in rows}


def get_store_for_user() -> str | None:
	"""Best-effort lookup: store the current user belongs to (via CH Store User)."""
	user = frappe.session.user
	if not user or user in ("Administrator", "Guest"):
		return None
	store = frappe.db.get_value(
		"CH Store User",
		{"user": user},
		"parent",
	)
	return store


# ──────────────────────────────────────────────────────────────────────────
# 3. The transfer engine
# ──────────────────────────────────────────────────────────────────────────

def transfer_between_bins(
	store: str,
	item_code: str,
	qty: float,
	from_bin_type: str,
	to_bin_type: str,
	reason: str | None = None,
	serial_no: str | None = None,
	batch_no: str | None = None,
	posting_date=None,
	submit: bool = True,
) -> str:
	"""Create a Stock Entry (Material Transfer) between two bins of one store.

	Returns the Stock Entry name. Raises if anything is invalid.
	"""
	if from_bin_type == to_bin_type:
		frappe.throw(_("Source and destination bin cannot be the same."))

	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than zero."))

	from_wh = get_store_bin(store, from_bin_type)
	to_wh = get_store_bin(store, to_bin_type)

	company = frappe.db.get_value("CH Store", store, "company")
	if not company:
		frappe.throw(_("Store {0} has no company configured.").format(store))

	# Reason validation (optional but recommended).
	reason_doc = None
	if reason:
		if not frappe.db.exists("CH Bin Transfer Reason", reason):
			frappe.throw(_("Bin Transfer Reason {0} does not exist.").format(reason))
		reason_doc = frappe.db.get_value(
			"CH Bin Transfer Reason",
			reason,
			["target_bin_type", "source_bin_type", "requires_serial", "disabled"],
			as_dict=True,
		)
		if reason_doc.disabled:
			frappe.throw(_("Bin Transfer Reason {0} is disabled.").format(reason))
		if reason_doc.target_bin_type and reason_doc.target_bin_type != to_bin_type:
			frappe.throw(
				_("Reason {0} requires destination bin {1}, got {2}.").format(
					reason, reason_doc.target_bin_type, to_bin_type
				)
			)
		if reason_doc.source_bin_type and reason_doc.source_bin_type != from_bin_type:
			frappe.throw(
				_("Reason {0} requires source bin {1}, got {2}.").format(
					reason, reason_doc.source_bin_type, from_bin_type
				)
			)
		if reason_doc.requires_serial and not serial_no:
			frappe.throw(_("Reason {0} requires a serial number.").format(reason))

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Transfer"
	se.purpose = "Material Transfer"
	se.company = company
	se.from_warehouse = from_wh
	se.to_warehouse = to_wh
	if posting_date:
		se.posting_date = posting_date

	# Stamp the reason if the custom field exists.
	if frappe.db.exists("Custom Field", {"dt": "Stock Entry", "fieldname": "ch_bin_transfer_reason"}):
		se.ch_bin_transfer_reason = reason
		se.ch_from_bin_type = from_bin_type
		se.ch_to_bin_type = to_bin_type
		se.ch_store = store

	row = se.append("items", {})
	row.item_code = item_code
	row.qty = qty
	row.s_warehouse = from_wh
	row.t_warehouse = to_wh
	if serial_no:
		row.serial_no = serial_no
	if batch_no:
		row.batch_no = batch_no

	se.flags.ignore_permissions = True
	se.insert()
	if submit:
		se.submit()
	frappe.db.commit()
	return se.name


# ──────────────────────────────────────────────────────────────────────────
# 4. POS-facing whitelisted APIs
# ──────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def pos_bin_transfer(
	item_code: str,
	qty: float,
	reason: str,
	from_bin_type: str | None = None,
	to_bin_type: str | None = None,
	store: str | None = None,
	serial_no: str | None = None,
	batch_no: str | None = None,
) -> dict:
	"""POS-friendly wrapper: figure out store from user, then transfer.

	`reason` is mandatory from POS so every move is auditable.
	If `from_bin_type` / `to_bin_type` are omitted, they're pulled from the
	reason's source_bin_type / target_bin_type fields.
	"""
	store = store or get_store_for_user()
	if not store:
		frappe.throw(_("Cannot determine the store for current user. Pass store explicitly."))

	if not reason:
		frappe.throw(_("Reason is required for POS bin transfers."))

	# Pull defaults from reason
	r = frappe.db.get_value(
		"CH Bin Transfer Reason",
		reason,
		["source_bin_type", "target_bin_type"],
		as_dict=True,
	)
	if not r:
		frappe.throw(_("Unknown reason {0}").format(reason))

	from_bin_type = from_bin_type or r.source_bin_type
	to_bin_type = to_bin_type or r.target_bin_type
	if not from_bin_type:
		frappe.throw(_("Source bin not specified and reason has no default."))
	if not to_bin_type:
		frappe.throw(_("Target bin not specified and reason has no default."))

	se_name = transfer_between_bins(
		store=store,
		item_code=item_code,
		qty=qty,
		from_bin_type=from_bin_type,
		to_bin_type=to_bin_type,
		reason=reason,
		serial_no=serial_no,
		batch_no=batch_no,
	)
	return {
		"stock_entry": se_name,
		"store": store,
		"from_bin": from_bin_type,
		"to_bin": to_bin_type,
		"qty": qty,
		"reason": reason,
	}


@frappe.whitelist()
def get_bin_transfer_reasons(target_bin_type: str | None = None) -> list:
	"""Return active reasons, optionally filtered by destination bin."""
	filters = {"disabled": 0}
	if target_bin_type:
		filters["target_bin_type"] = target_bin_type
	return frappe.get_all(
		"CH Bin Transfer Reason",
		filters=filters,
		fields=[
			"name",
			"reason_name",
			"source_bin_type",
			"target_bin_type",
			"requires_serial",
			"description",
		],
		order_by="target_bin_type, reason_name",
	)


@frappe.whitelist()
def get_pos_bin_summary(store: str | None = None, item_code: str | None = None) -> dict:
	"""Return current qty per bin for a store (optionally one item)."""
	store = store or get_store_for_user()
	if not store:
		frappe.throw(_("Cannot determine store for current user."))

	bins = get_store_bins(store)
	result = {"store": store, "bins": []}

	for bin_type, wh in bins.items():
		filters = {"wh": wh}
		if item_code:
			filters["item_code"] = item_code
		row = frappe.db.sql(
			"""
			SELECT
				IFNULL(SUM(actual_qty), 0) AS qty,
				COUNT(DISTINCT item_code) AS item_count
			FROM `tabBin`
			WHERE warehouse = %(wh)s
				{item_filter}
			""".format(item_filter="AND item_code = %(item_code)s" if item_code else ""),
			filters,
			as_dict=True,
		)
		result["bins"].append(
			{
				"bin_type": bin_type,
				"warehouse": wh,
				"qty": flt(row[0]["qty"]) if row else 0.0,
				"items": int(row[0]["item_count"]) if row else 0,
			}
		)
	return result


# ──────────────────────────────────────────────────────────────────────────
# 5. Default reason seeding (called from setup.after_install/after_migrate)
# ──────────────────────────────────────────────────────────────────────────

DEFAULT_REASONS = [
	# (reason_name, target_bin, source_bin, requires_serial, description)
	("Customer Pre-Booking", "Reserved", "Sellable", 0, "Hold stock against a confirmed booking."),
	("Release Reservation", "Sellable", "Reserved", 0, "Free up reserved stock back to sellable."),
	("Damaged on Shop Floor", "Damaged", "Sellable", 0, "Item broken / damaged while on shelf."),
	("Damaged In Transit", "Damaged", "In-Transit", 0, "Damaged during transfer from zone."),
	("Customer Return - Sellable", "Sellable", None, 0, "Returned item is resellable."),
	("Customer Return - Damaged", "Damaged", None, 0, "Returned item is damaged."),
	("Received From Zone Hub", "Sellable", "In-Transit", 0, "Goods receipt completion."),
	("Dispatch To Zone Hub", "In-Transit", "Sellable", 0, "Outbound transfer to hub."),
	("Dispose Damaged Item", "Disposed", "Damaged", 1, "Scrap / write-off; requires serial."),
	("Repaired - Back To Sellable", "Sellable", "Damaged", 0, "Repaired and re-graded."),
]


def seed_default_reasons():
	"""Idempotently create default bin-transfer reasons."""
	if not frappe.db.table_exists("CH Bin Transfer Reason"):
		return
	for name, tgt, src, ser, desc in DEFAULT_REASONS:
		if frappe.db.exists("CH Bin Transfer Reason", name):
			continue
		d = frappe.new_doc("CH Bin Transfer Reason")
		d.reason_name = name
		d.target_bin_type = tgt
		d.source_bin_type = src
		d.requires_serial = ser
		d.description = desc
		d.insert(ignore_permissions=True)
