"""Bin transfer / stock allocation engine for CH Store warehouses.

Canonical bins on every store (see ch_store.STORE_BIN_TYPES):
	Sellable, Damaged, Demo, Buyback

Transit between stores uses the company-level ``Goods In Transit - <abbr>``
warehouse that ERPNext provisions for every Company, not per-store transit
bins. Soft reservations live in reservation tables. Disposal posts a
write-off Stock Entry against a disposal expense account. There is no
``In-Transit`` / ``Reserved`` / ``Disposed`` bin in the canonical model.

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
from frappe.utils import flt, get_datetime
from erpnext.stock.serial_batch_bundle import SerialBatchCreation

from ch_item_master.config import (
	get_int_setting,
	is_privileged_user,
	require_role_setting,
)
from ch_item_master.security import ensure_company_access


_BIN_VIEW_ROLES = ("Stock Manager", "Stock User", "Store Manager", "Store Executive")
_BIN_TRANSFER_ROLES = ("Stock Manager", "Stock User", "Store Manager")


def _require_named_permission(doctype: str, permission_type: str = "read") -> None:
	if is_privileged_user():
		return
	if not frappe.has_permission(doctype, ptype=permission_type):
		frappe.throw(
			_("You do not have {0} permission for {1}.").format(permission_type, doctype),
			frappe.PermissionError,
		)


def _authorized_store(store: str, *, transfer: bool = False):
	if not store:
		frappe.throw(_("A store is required."), frappe.ValidationError)
	require_role_setting(
		"bin_transfer_roles" if transfer else "bin_view_roles",
		_BIN_TRANSFER_ROLES if transfer else _BIN_VIEW_ROLES,
		action=_("transfer stock between bins") if transfer else _("view bin inventory"),
	)
	store_doc = frappe.get_doc("CH Store", store)
	if store_doc.disabled:
		frappe.throw(_("The selected store is disabled."), frappe.ValidationError)
	if is_privileged_user():
		return store_doc

	if not frappe.has_permission("CH Store", ptype="read", doc=store_doc):
		frappe.throw(_("You do not have permission to view this store."), frappe.PermissionError)
	ensure_company_access(store_doc.company)
	try:
		from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
	except (ImportError, ModuleNotFoundError):
		frappe.throw(_("Store scope validation is unavailable."), frappe.PermissionError)
	assert_user_has_store_scope(
		store=store_doc.name,
		warehouse=store_doc.warehouse,
		company=store_doc.company,
		msg=_("The selected store is outside your assigned scope."),
	)
	return store_doc


def _validate_transfer_references(item_code, from_warehouse, serial_no=None, batch_no=None):
	_require_named_permission("Item")
	item = frappe.db.get_value("Item", item_code, ["name", "disabled"], as_dict=True)
	if not item or item.disabled:
		frappe.throw(_("The selected item is missing or disabled."), frappe.ValidationError)

	serials = [value.strip() for value in str(serial_no or "").split("\n") if value.strip()]
	if serials:
		_require_named_permission("Serial No")
		placeholders = ", ".join(["%s"] * len(serials))
		rows = frappe.db.sql(
			f"""
			SELECT name, item_code, warehouse
			FROM `tabSerial No`
			WHERE name IN ({placeholders})
			FOR UPDATE
			""",
			tuple(serials),
			as_dict=True,
		)
		if len(rows) != len(set(serials)):
			frappe.throw(_("One or more serial numbers do not exist."), frappe.ValidationError)
		if any(row.item_code != item_code or row.warehouse != from_warehouse for row in rows):
			frappe.throw(
				_("Every serial number must belong to the selected item and source bin."),
				frappe.ValidationError,
			)

	if batch_no:
		_require_named_permission("Batch")
		batch = frappe.db.get_value("Batch", batch_no, ["item", "disabled"], as_dict=True)
		if not batch or batch.item != item_code or batch.disabled:
			frappe.throw(_("The selected batch is invalid for this item."), frappe.ValidationError)
	return serials


# ──────────────────────────────────────────────────────────────────────────
# 1. Bin types and default allocation rules
# ──────────────────────────────────────────────────────────────────────────

# Canonical list of bin types (mirrors ch_store.STORE_BIN_TYPES).
# In-Transit / Disposed / Reserved were removed in Path B Phase 3
# (2026-06-29) along with their per-store warehouses, in favour of:
#   - Goods In Transit - <abbr>  (company-level, for transit)
#   - reservation tables         (soft reservations)
#   - write-off Stock Entry      (disposal)
BIN_TYPES = ("Sellable", "Damaged", "Demo", "Buyback")

# Default rules: for each business event, where do items move?
# Format: { event: (from_bin_type, to_bin_type) }
# ``None`` means "decided at runtime" (e.g. POS user picks).
# NOTE: this dict is currently declarative — the live API
# (``transfer_between_bins`` / ``pos_bin_transfer``) takes explicit
# from/to bin types from the caller. It exists as a contract reference
# for downstream features (e.g. workflow buttons) that need to map a
# business event to the canonical bin pair.
DEFAULT_ALLOCATION_RULES = {
	# Outbound to customer — always debits Sellable.
	"pos_sale": ("Sellable", None),               # leaves the store entirely
	"sales_return": (None, "Sellable"),           # back into Sellable by default
	# Internal bin moves within a single store.
	"mark_damaged": ("Sellable", "Damaged"),
	"return_to_sellable": ("Damaged", "Sellable"),     # repaired / re-graded
	"buyback_intake": (None, "Buyback"),               # used-device intake
	"buyback_to_sellable": ("Buyback", "Sellable"),    # refurbished re-sale
	"to_demo": ("Sellable", "Demo"),                   # promote to display unit
	"from_demo": ("Demo", "Sellable"),                 # retire demo back to floor
}


# ──────────────────────────────────────────────────────────────────────────
# 2. Bin lookup helpers
# ──────────────────────────────────────────────────────────────────────────

def get_store_bin(store: str, bin_type: str) -> str:
	"""Return warehouse name for the given store + bin_type. Throws if missing.

	Post-v12 collapse: the Sellable bin IS the store's base warehouse (no
	separate child). We resolve via CH Store.warehouse for ``Sellable`` and
	fall back to the legacy lookup (ch_store + ch_bin_type) for other bins.
	"""
	if not store or not bin_type:
		frappe.throw(_("Store and bin type are required."))

	if bin_type not in BIN_TYPES:
		frappe.throw(
			_("Invalid bin type {0}. Must be one of: {1}").format(bin_type, ", ".join(BIN_TYPES))
		)

	if bin_type == "Sellable":
		base = frappe.db.get_value("CH Store", store, "warehouse")
		if base:
			return base
		# Backward-compat: site not yet migrated.
		legacy = frappe.db.get_value(
			"Warehouse",
			{"ch_store": store, "ch_bin_type": "Sellable", "disabled": 0},
			"name",
		)
		if legacy:
			return legacy
		frappe.throw(
			_("Store {0} has no base warehouse configured.").format(frappe.bold(store)),
			title=_("Bin Missing"),
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
	bins = {}
	base = frappe.db.get_value("CH Store", store, "warehouse")
	if base:
		bins["Sellable"] = base
	rows = frappe.get_all(
		"Warehouse",
		filters={"ch_store": store, "ch_bin_type": ["!=", ""], "disabled": 0},
		fields=["ch_bin_type", "name"],
	)
	bins.update({r.ch_bin_type: r.name for r in rows})
	return bins


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
	store_doc = _authorized_store(store, transfer=True)
	for doctype, permission_type in (
		("Stock Entry", "create"),
		("Stock Entry", "submit"),
		("CH Bin Transfer Reason", "read"),
		("Warehouse", "read"),
	):
		_require_named_permission(doctype, permission_type)

	if from_bin_type == to_bin_type:
		frappe.throw(_("Source and destination bin cannot be the same."))

	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than zero."))
	quantity_limit = get_int_setting("bin_transfer_quantity_limit", 1000, minimum=1)
	if qty > quantity_limit:
		frappe.throw(
			_("Quantity cannot exceed the configured limit of {0}.").format(quantity_limit),
			frappe.ValidationError,
		)

	from_wh = get_store_bin(store, from_bin_type)
	to_wh = get_store_bin(store, to_bin_type)

	company = store_doc.company
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
	elif not is_privileged_user():
		frappe.throw(_("A transfer reason is required."), frappe.ValidationError)

	serials = _validate_transfer_references(item_code, from_wh, serial_no, batch_no)
	bin_name = frappe.db.get_value(
		"Bin",
		{"item_code": item_code, "warehouse": from_wh},
		"name",
		for_update=True,
	)
	if not bin_name:
		frappe.throw(_("No stock exists for the selected item in the source bin."), frappe.ValidationError)

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

	# Intra-store bin reclassification (both warehouses belong to the same
	# CH Store). This is NOT an inter-store transfer, so it must not go
	# through the in-transit logistics flow. Set the flag that the
	# ch_erp15 procurement guardrail reads before blocking direct MT submit.
	se.flags.ignore_procurement_guardrails = True
	se.insert()

	# In v16 with Stock Settings.use_serial_batch_fields = 1, old-style
	# row.serial_no may not guarantee exact-serial movement. Attach an explicit
	# outward bundle so submit consumes the intended serial(s).
	if serial_no and frappe.get_single_value("Stock Settings", "use_serial_batch_fields"):
		if len(serials) != int(qty):
			frappe.throw(
				_("Serial count {0} must match Qty {1} for serialized transfer.").format(
					len(serials), int(qty)
				)
			)

		posting_dt = get_datetime(f"{se.posting_date} {se.posting_time}")
		bundle = SerialBatchCreation(
			{
				"item_code": item_code,
				"warehouse": from_wh,
				"voucher_type": "Stock Entry",
				"voucher_no": se.name,
				"voucher_detail_no": row.name,
				"qty": -qty,
				"type_of_transaction": "Outward",
				"company": company,
				"posting_datetime": posting_dt,
				"do_not_submit": True,
			}
		).make_serial_and_batch_bundle(serial_nos=serials)

		if bundle:
			row.serial_and_batch_bundle = bundle.name if hasattr(bundle, "name") else bundle
			row.serial_no = ""
			se.save()

	if submit:
		se.submit()
	return se.name


# ──────────────────────────────────────────────────────────────────────────
# 4. POS-facing whitelisted APIs
# ──────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
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
	require_role_setting("bin_view_roles", _BIN_VIEW_ROLES, action=_("view bin transfer reasons"))
	_require_named_permission("CH Bin Transfer Reason", "read")
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
	_authorized_store(store)
	_require_named_permission("Warehouse", "read")
	_require_named_permission("Bin", "read")
	if item_code:
		_require_named_permission("Item", "read")

	bins = get_store_bins(store)
	result = {"store": store, "bins": []}
	params = {"warehouses": tuple(bins.values())}
	if item_code:
		params["item_code"] = item_code
	summary_rows = frappe.db.sql(
		"""
		SELECT
			warehouse,
			IFNULL(SUM(actual_qty), 0) AS qty,
			COUNT(DISTINCT item_code) AS item_count
		FROM `tabBin`
		WHERE warehouse IN %(warehouses)s
			{item_filter}
		GROUP BY warehouse
		""".format(item_filter="AND item_code = %(item_code)s" if item_code else ""),
		params,
		as_dict=True,
	) if bins else []
	summary_by_warehouse = {row.warehouse: row for row in summary_rows}

	for bin_type, wh in bins.items():
		row = summary_by_warehouse.get(wh) or {}
		result["bins"].append(
			{
				"bin_type": bin_type,
				"warehouse": wh,
				"qty": flt(row.get("qty", 0)),
				"items": int(row.get("item_count", 0)),
			}
		)
	return result


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_bin_items(doctype, txt, searchfield, start, page_len, filters):
	"""Link-field query: items that currently sit in a store's source bin.

	Wired to the Bin Transfer dialog's ``item_code`` picker so the user can
	only choose items that actually have positive stock in the selected
	``from_bin_type`` of the selected store (mirrors the POS Sell menu, which
	only lists sellable-bin stock). Returns ``[]`` until both store and bin
	are chosen, or if the bin is not provisioned.
	"""
	filters = filters or {}
	store = filters.get("store")
	bin_type = filters.get("from_bin_type") or filters.get("bin_type")
	if not store or not bin_type:
		return []
	_authorized_store(store)
	for doctype in ("Warehouse", "Bin", "Item"):
		_require_named_permission(doctype, "read")

	try:
		warehouse = get_store_bin(store, bin_type)
	except Exception:
		# Bin not provisioned for this store — show nothing rather than erroring.
		return []

	query_limit = get_int_setting("bin_query_limit", 200, minimum=1)
	page_len = max(1, min(int(page_len or 20), query_limit))
	start = max(0, int(start or 0))
	like = f"%{txt or ''}%"
	return frappe.db.sql(
		"""
		SELECT b.item_code, i.item_name
		FROM `tabBin` b
		INNER JOIN `tabItem` i ON i.name = b.item_code
		WHERE b.warehouse = %(wh)s
			AND b.actual_qty > 0
			AND i.disabled = 0
			AND (b.item_code LIKE %(txt)s OR i.item_name LIKE %(txt)s)
		ORDER BY b.item_code
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"wh": warehouse,
			"txt": like,
			"start": start,
			"page_len": page_len,
		},
	)


@frappe.whitelist()
def get_store_bin_serials(
	bin_type: str,
	store: str | None = None,
	item_code: str | None = None,
	search_text: str | None = None,
	limit: int = 100,
) -> dict:
	"""Return serial-level rows currently sitting in one store bin.

	Used by the CH POS Bin Manager tabs.
	"""
	store = store or get_store_for_user()
	if not store:
		frappe.throw(_("Cannot determine store for current user."))
	_authorized_store(store)
	for doctype in ("Warehouse", "Serial No", "Item"):
		_require_named_permission(doctype, "read")
	if frappe.db.table_exists("CH Stock Bin"):
		_require_named_permission("CH Stock Bin", "read")

	warehouse = get_store_bin(store, bin_type)
	limit = max(
		1,
		min(int(limit or 100), get_int_setting("bin_query_limit", 200, minimum=1)),
	)

	rows = []
	if frappe.db.table_exists("CH Stock Bin"):
		conditions = ["sb.warehouse = %(warehouse)s"]
		params = {"warehouse": warehouse, "limit": limit}

		if item_code:
			conditions.append("sb.item_code = %(item_code)s")
			params["item_code"] = item_code

		if search_text:
			st = f"%{search_text.strip()}%"
			conditions.append(
				"(sb.serial_no LIKE %(search)s OR sb.item_code LIKE %(search)s "
				"OR IFNULL(sn.item_name, i.item_name) LIKE %(search)s)"
			)
			params["search"] = st

		rows = frappe.db.sql(
			"""
			SELECT
				sb.serial_no,
				sb.item_code,
				IFNULL(sn.item_name, i.item_name) AS item_name,
				IFNULL(sn.status, 'Active') AS status,
				sb.warehouse
			FROM `tabCH Stock Bin` sb
			LEFT JOIN `tabSerial No` sn ON sn.name = sb.serial_no
			LEFT JOIN `tabItem` i ON i.name = sb.item_code
			WHERE {where_clause}
			ORDER BY IFNULL(sb.moved_at, IFNULL(sb.modified, sb.creation)) DESC, sb.serial_no DESC
			LIMIT %(limit)s
			""".format(where_clause=" AND ".join(conditions)),
			params,
			as_dict=True,
		)

	# Backward-compatible fallback for sites that do not have CH Stock Bin rows yet.
	if not rows:
		conditions = ["warehouse = %(warehouse)s"]
		params = {"warehouse": warehouse, "limit": limit}

		if item_code:
			conditions.append("item_code = %(item_code)s")
			params["item_code"] = item_code

		if search_text:
			st = f"%{search_text.strip()}%"
			conditions.append("(name LIKE %(search)s OR item_code LIKE %(search)s OR item_name LIKE %(search)s)")
			params["search"] = st

		rows = frappe.db.sql(
			"""
			SELECT
				name AS serial_no,
				item_code,
				item_name,
				status,
				warehouse
			FROM `tabSerial No`
			WHERE {where_clause}
			ORDER BY modified DESC
			LIMIT %(limit)s
			""".format(where_clause=" AND ".join(conditions)),
			params,
			as_dict=True,
		)

	return {
		"store": store,
		"bin_type": bin_type,
		"warehouse": warehouse,
		"count": len(rows),
		"serials": rows,
	}


# In-transit is company-level (Goods In Transit warehouse), surfaced read-only
# per store from the manifest that owns the movement — the canonical model has
# no per-store In-Transit bin (removed in Path B Phase 3).
_IN_TRANSIT_MANIFEST_STATES = ("Assigned", "Pickup Started", "In Transit", "Delivered")


@frappe.whitelist()
def get_store_in_transit(store: str | None = None, limit: int = 200) -> dict:
	"""Read-only: stock currently in transit TO this store.

	Resolved from active (dispatched, not-yet-received) CH Transfer Manifests
	whose destination is this store. Transit stock physically lives in the
	company ``Goods In Transit`` warehouse; the destination + ETA come from the
	manifest. Returns rows shaped like get_store_bin_serials for the UI.
	"""
	store = store or get_store_for_user()
	if not store:
		frappe.throw(_("Cannot determine store for current user."))
	_authorized_store(store)
	for doctype in ("CH Transfer Manifest", "Stock Entry", "Warehouse"):
		_require_named_permission(doctype, "read")
	limit = max(1, min(int(limit or 200), get_int_setting("bin_query_limit", 200, minimum=1)))

	# A manifest is "to this store" if it names the store directly, or its
	# destination warehouse belongs to the store (some manifests set only the
	# warehouse). Collect the store's warehouses for the warehouse match.
	store_whs = set(filter(None, [frappe.db.get_value("CH Store", store, "warehouse")]))
	store_whs.update(frappe.get_all("Warehouse", filters={"ch_store": store}, pluck="name"))

	or_filters = [{"destination_store": store}]
	if store_whs:
		or_filters.append({"destination_warehouse": ["in", list(store_whs)]})

	manifests = frappe.get_all(
		"CH Transfer Manifest",
		filters={"docstatus": 1, "status": ["in", _IN_TRANSIT_MANIFEST_STATES]},
		or_filters=or_filters,
		fields=["name", "status", "source_store", "source_warehouse",
				"estimated_delivery_date", "driver_name"],
		order_by="modified desc",
		limit_page_length=limit,
	)
	manifest_by_name = {manifest.name: manifest for manifest in manifests}
	manifest_items = frappe.get_all(
		"CH Transfer Manifest Item",
		filters={
			"parent": ("in", tuple(manifest_by_name)),
			"stock_entry": ("is", "set"),
		},
		fields=["parent", "stock_entry"],
		order_by="parent, idx",
		limit_page_length=limit * 5,
	) if manifest_by_name else []
	manifest_by_stock_entry = {}
	for item in manifest_items:
		manifest_by_stock_entry.setdefault(item.stock_entry, manifest_by_name[item.parent])
	stock_rows = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": ("in", tuple(manifest_by_stock_entry))},
		fields=["parent", "item_code", "item_name", "qty", "serial_no"],
		order_by="parent, idx",
		limit_page_length=limit,
	) if manifest_by_stock_entry else []
	rows = []
	for item in stock_rows:
		manifest = manifest_by_stock_entry[item.parent]
		rows.append({
			"serial_no": (item.serial_no or "").strip().split("\n")[0],
			"item_code": item.item_code,
			"item_name": item.item_name,
			"qty": item.qty,
			"manifest": manifest.name,
			"source": manifest.source_store or manifest.source_warehouse or "",
			"eta": manifest.estimated_delivery_date,
			"status": manifest.status,
		})
	return {"warehouse": _("Goods In Transit"), "count": len(rows), "serials": rows}


@frappe.whitelist()
def get_serial_bin_context(serial_no: str, store: str | None = None) -> dict:
	"""Resolve a serial to its current store bin context (item + bin type)."""
	store = store or get_store_for_user()
	if not store:
		frappe.throw(_("Cannot determine store for current user."))
	if not serial_no:
		frappe.throw(_("Serial number is required."))
	_authorized_store(store)
	for doctype in ("Warehouse", "Serial No", "Item"):
		_require_named_permission(doctype, "read")
	if frappe.db.table_exists("CH Stock Bin"):
		_require_named_permission("CH Stock Bin", "read")

	# Prefer CH Stock Bin overlay for exact, bin-level context.
	if frappe.db.table_exists("CH Stock Bin"):
		ctx = frappe.db.sql(
			"""
			SELECT
				sb.serial_no,
				sb.item_code,
				IFNULL(sn.item_name, i.item_name) AS item_name,
				sb.warehouse,
				sb.bin_type,
				w.ch_store AS store,
				IFNULL(sn.status, '') AS status
			FROM `tabCH Stock Bin` sb
			INNER JOIN `tabWarehouse` w ON w.name = sb.warehouse
			LEFT JOIN `tabSerial No` sn ON sn.name = sb.serial_no
			LEFT JOIN `tabItem` i ON i.name = sb.item_code
			WHERE sb.serial_no = %(serial_no)s
			  AND w.ch_store = %(store)s
			ORDER BY IFNULL(sb.moved_at, IFNULL(sb.modified, sb.creation)) DESC
			LIMIT 1
			""",
			{"serial_no": serial_no, "store": store},
			as_dict=True,
		)
		if ctx:
			return ctx[0]

	serial = frappe.db.get_value(
		"Serial No",
		serial_no,
		["name", "item_code", "item_name", "warehouse", "status"],
		as_dict=True,
	)
	if not serial or not serial.warehouse:
		return {}

	store_base = frappe.db.get_value("CH Store", store, "warehouse")
	if store_base and serial.warehouse == store_base:
		return {
			"serial_no": serial.name,
			"item_code": serial.item_code,
			"item_name": serial.item_name,
			"warehouse": serial.warehouse,
			"bin_type": "Sellable",
			"store": store,
			"status": serial.status,
		}

	wh = frappe.db.get_value(
		"Warehouse",
		{"name": serial.warehouse, "ch_store": store},
		["name", "ch_bin_type", "ch_store"],
		as_dict=True,
	)
	if not wh:
		return {}

	return {
		"serial_no": serial.name,
		"item_code": serial.item_code,
		"item_name": serial.item_name,
		"warehouse": wh.name,
		"bin_type": wh.ch_bin_type,
		"store": wh.ch_store,
		"status": serial.status,
	}


# ──────────────────────────────────────────────────────────────────────────
# 5. Default reason seeding (called from setup.after_install/after_migrate)
# ──────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────
# 6. One-shot backfill: move existing stock from legacy parent warehouses
#    into the per-store Sellable bin.
# ──────────────────────────────────────────────────────────────────────────

def backfill_existing_stock_to_sellable(store: str | None = None, dry_run: int = 0) -> dict:
	"""For each CH Store, move all stock currently sitting in the parent store
	warehouse (or any warehouse linked to the store but not a bin) into that
	store's Sellable bin via a single Stock Entry per source warehouse.

	Args:
		store: limit to a single CH Store (else all enabled stores).
		dry_run: if truthy, only report what would be moved.

	Returns: {"moved": [...], "skipped": [...], "errors": [...]}.
	"""
	dry_run = int(dry_run or 0)
	moved, skipped, errors = [], [], []
	if not store:
		frappe.throw(
			_("Select one store for each legacy stock backfill run."),
			frappe.ValidationError,
		)
	st = _authorized_store(store, transfer=True)
	if not dry_run:
		_require_named_permission("Stock Entry", "create")
		_require_named_permission("Stock Entry", "submit")
	maintenance_limit = min(get_int_setting("bin_query_limit", 200, minimum=1), 2000)
	has_transfer_reason_fields = frappe.get_meta("Stock Entry").has_field("ch_bin_transfer_reason")

	if not st.warehouse:
		return {
			"moved": moved,
			"skipped": [{"store": st.name, "reason": "no parent warehouse"}],
			"errors": errors,
		}

	sellable = frappe.db.get_value(
		"Warehouse",
		{"ch_store": st.name, "ch_bin_type": "Sellable", "disabled": 0},
		"name",
	) or st.warehouse
	if not sellable:
		return {
			"moved": moved,
			"skipped": skipped,
			"errors": [{"store": st.name, "reason": "missing Sellable bin"}],
		}

	linked_whs = frappe.get_all(
		"Warehouse",
		filters={"ch_store": st.name, "disabled": 0},
		fields=["name", "ch_bin_type"],
		order_by="name asc",
		limit_page_length=maintenance_limit + 1,
	)
	if len(linked_whs) > maintenance_limit:
		frappe.throw(
			_("Store {0} exceeds the configured Bin Query Row Limit. Increase it only for a controlled maintenance run.").format(st.name),
			frappe.ValidationError,
		)
	source_whs = {w.name for w in linked_whs if not (w.ch_bin_type or "").strip()}
	source_whs.add(st.warehouse)
	source_whs.discard(sellable)

	for src in sorted(source_whs):
		bins = frappe.get_all(
			"Bin",
			filters={"warehouse": src, "actual_qty": (">", 0)},
			fields=["item_code", "actual_qty"],
			order_by="item_code asc",
			limit_page_length=maintenance_limit + 1,
		)
		if not bins:
			continue
		if len(bins) > maintenance_limit:
			errors.append({
				"store": st.name,
				"from": src,
				"error": _("Positive-stock item count exceeds the configured Bin Query Row Limit."),
			})
			continue

		if dry_run:
			moved.append({
				"store": st.name, "from": src, "to": sellable,
				"items": len(bins), "dry_run": True,
			})
			continue

		serial_rows = frappe.get_all(
			"Serial No",
			filters={
				"item_code": ("in", tuple({row.item_code for row in bins})),
				"warehouse": src,
				"status": "Active",
			},
			fields=["name", "item_code"],
			order_by="item_code, name",
			limit_page_length=maintenance_limit + 1,
		)
		if len(serial_rows) > maintenance_limit:
			errors.append({
				"store": st.name,
				"from": src,
				"error": _("Active serial count exceeds the configured Bin Query Row Limit."),
			})
			continue
		serials_by_item = {}
		for serial in serial_rows:
			serials_by_item.setdefault(serial.item_code, []).append(serial.name)

		save_point = "ch_legacy_stock_backfill_source"
		frappe.db.savepoint(save_point=save_point)
		was_group = frappe.db.get_value("Warehouse", src, "is_group")
		try:
			if was_group:
				frappe.db.set_value("Warehouse", src, "is_group", 0, update_modified=False)
				frappe.clear_document_cache("Warehouse", src)

			se = frappe.new_doc("Stock Entry")
			se.stock_entry_type = "Material Transfer"
			se.purpose = "Material Transfer"
			se.company = st.company
			se.from_warehouse = src
			se.to_warehouse = sellable
			if has_transfer_reason_fields:
				se.ch_from_bin_type = ""
				se.ch_to_bin_type = "Sellable"
				se.ch_store = st.name
			for b in bins:
				row = se.append("items", {})
				row.item_code = b.item_code
				row.qty = flt(b.actual_qty)
				row.s_warehouse = src
				row.t_warehouse = sellable
				serials = serials_by_item.get(b.item_code, [])
				if serials:
					row.serial_no = "\n".join(serials)
			se.insert()
			se.submit()
			if was_group:
				frappe.db.set_value("Warehouse", src, "is_group", 1, update_modified=False)
				frappe.clear_document_cache("Warehouse", src)
			moved.append({
				"store": st.name, "from": src, "to": sellable,
				"items": len(bins), "stock_entry": se.name,
				"restored_group": bool(was_group),
			})
		except Exception as exc:
			frappe.db.rollback(save_point=save_point)
			frappe.clear_document_cache("Warehouse", src)
			errors.append({"store": st.name, "from": src, "error": str(exc)})

	return {"moved": moved, "skipped": skipped, "errors": errors}


# ──────────────────────────────────────────────────────────────────────────
# 7. Default reason seeding (called from setup.after_install/after_migrate)
# ──────────────────────────────────────────────────────────────────────────

DEFAULT_REASONS = [
	# (reason_name, target_bin, source_bin, requires_serial, description)
	# Damage handling.
	("Damaged on Shop Floor", "Damaged", "Sellable", 0, "Item broken / damaged while on shelf."),
	("Repaired - Back To Sellable", "Sellable", "Damaged", 0, "Repaired and re-graded."),
	# Customer returns.
	("Customer Return - Sellable", "Sellable", None, 0, "Returned item is resellable."),
	("Customer Return - Damaged", "Damaged", None, 0, "Returned item is damaged."),
	# Buyback workflow.
	("Buyback Intake", "Buyback", None, 1, "Used device received from customer."),
	("Buyback Refurbished", "Sellable", "Buyback", 1, "Refurbished buyback unit ready for re-sale."),
	# Demo / display units.
	("Promote To Demo", "Demo", "Sellable", 1, "Move a Sellable unit to in-store demo."),
	("Retire Demo", "Sellable", "Demo", 1, "Retire a demo unit back to the sales floor."),
	# NOTE: legacy reasons (Customer Pre-Booking, Release Reservation,
	# Damaged In Transit, Received/Dispatch To Zone Hub, Dispose Damaged
	# Item) were retired in Path B Phase 3 (2026-06-29) when the
	# In-Transit / Reserved / Disposed bin types were removed from the
	# canonical model. Reservations live in reservation tables, transit
	# uses the company-level Goods In Transit warehouse, and disposal
	# posts a write-off Stock Entry.
]


def seed_default_reasons():
	"""Idempotently create default bin-transfer reasons."""
	if not frappe.db.table_exists("CH Bin Transfer Reason"):
		return
	existing = set(frappe.get_all(
		"CH Bin Transfer Reason",
		filters={"name": ("in", [reason[0] for reason in DEFAULT_REASONS])},
		pluck="name",
	))
	for name, tgt, src, ser, desc in DEFAULT_REASONS:
		if name in existing:
			continue
		d = frappe.new_doc("CH Bin Transfer Reason")
		d.reason_name = name
		d.target_bin_type = tgt
		d.source_bin_type = src
		d.requires_serial = ser
		d.description = desc
		d.insert(ignore_permissions=True)
