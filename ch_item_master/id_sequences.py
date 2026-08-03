import hashlib

import frappe
from frappe.model.naming import getseries


NUMERIC_ID_SERIES = {
	"warranty_claim": "CH-ID-WARRANTY-CLAIM-",
	"active_vas_plan": "CH-ID-ACTIVE-VAS-PLAN-",
	"model": "CH-ID-MODEL-",
	"store": "CH-ID-STORE-",
	"payment_method": "CH-ID-PAYMENT-METHOD-",
	"manufacturer": "CH-ID-MANUFACTURER-",
	"brand": "CH-ID-BRAND-",
	"item_group": "CH-ID-ITEM-GROUP-",
	"customer": "CH-ID-CUSTOMER-",
	"category": "CH-ID-CATEGORY-",
	"sub_category": "CH-ID-SUB-CATEGORY-",
	"feature": "CH-ID-FEATURE-",
	"feature_group": "CH-ID-FEATURE-GROUP-",
	"price_channel": "CH-ID-PRICE-CHANNEL-",
	"warranty_plan": "CH-ID-WARRANTY-PLAN-",
	"loyalty_transaction": "CH-ID-LOYALTY-TRANSACTION-",
	"customer_device": "CH-ID-CUSTOMER-DEVICE-",
}


def _ensure_series(key: str) -> None:
	frappe.db.sql(
		"INSERT IGNORE INTO `tabSeries` (`name`, `current`) VALUES (%s, 0)",
		(key,),
	)


def next_numeric_id(sequence: str) -> int:
	key = NUMERIC_ID_SERIES[sequence]
	_ensure_series(key)
	return int(getseries(key, 9))


# Where each numeric-ID counter is actually stored, so a lagging counter can be
# detected and fast-forwarded. Only sequences whose target field carries a
# unique index really need this, but keeping the map complete means a field
# that gains `unique` later is covered without another code change.
NUMERIC_ID_TARGETS = {
	"warranty_claim": ("CH Warranty Claim", "claim_id"),
	"active_vas_plan": ("Active VAS Plans", "sold_plan_id"),
	"model": ("CH Model", "model_id"),
	"store": ("CH Store", "store_id"),
	"payment_method": ("CH Payment Method", "payment_method_id"),
	"manufacturer": ("Manufacturer", "manufacturer_id"),
	"brand": ("Brand", "brand_id"),
	"item_group": ("Item Group", "item_group_id"),
	"customer": ("Customer", "ch_customer_id"),
	"category": ("CH Category", "category_id"),
	"sub_category": ("CH Sub Category", "sub_category_id"),
	"feature": ("CH Feature", "feature_id"),
	"feature_group": ("CH Feature Group", "feature_group_id"),
	"price_channel": ("CH Price Channel", "channel_id"),
	"warranty_plan": ("CH Warranty Plan", "warranty_plan_id"),
	"loyalty_transaction": ("CH Loyalty Transaction", "loyalty_txn_id"),
	"customer_device": ("CH Customer Device", "device_id"),
}

_MAX_ID_PROBES = 50


def sync_numeric_id_series(sequence: str) -> int:
	"""Fast-forward a counter past the highest ID already stored.

	`getseries` only knows its own counter. A restored database dump or a bulk
	import routinely lands rows whose IDs sit *above* `tabSeries.current` — the
	dump's data and its series row are not necessarily from the same instant.
	Every subsequent insert then dies on the unique index until the counter
	walks past the occupied range one failed transaction at a time. On
	2026-07-31 that surfaced as POS billing failing with "Device ID must be
	unique" (duplicate device_id 58589).

	GREATEST() means this only ever moves the counter forward, so it is safe to
	run concurrently with live inserts and safe to re-run.
	"""
	target = NUMERIC_ID_TARGETS.get(sequence)
	if not target:
		return 0
	doctype, fieldname = target
	if not frappe.db.table_exists(doctype):
		return 0
	meta = frappe.get_meta(doctype)
	if not meta.get_field(fieldname):
		return 0

	key = NUMERIC_ID_SERIES[sequence]
	_ensure_series(key)
	# doctype/fieldname come from the module-level map above, never from input.
	highest = frappe.db.sql(
		"SELECT MAX(`{0}`) FROM `tab{1}`".format(fieldname, doctype)
	)[0][0]
	highest = int(highest or 0)
	frappe.db.sql(
		"UPDATE `tabSeries` SET `current` = GREATEST(`current`, %s) WHERE `name` = %s",
		(highest, key),
	)
	return highest


def next_free_numeric_id(sequence: str) -> int:
	"""Allocate a numeric ID that is not already occupied.

	Prefer this over `next_numeric_id` for any field carrying a unique index.
	The happy path costs one extra indexed existence check; the counter is only
	re-synced when a collision actually proves it has fallen behind.
	"""
	candidate = next_numeric_id(sequence)
	target = NUMERIC_ID_TARGETS.get(sequence)
	if not target:
		return candidate

	doctype, fieldname = target
	if not frappe.db.exists(doctype, {fieldname: candidate}):
		return candidate

	# Collision proves the counter is behind the data. Jump past the whole
	# occupied range at once rather than burning one failed sale per ID.
	sync_numeric_id_series(sequence)
	for _ in range(_MAX_ID_PROBES):
		candidate = next_numeric_id(sequence)
		if not frappe.db.exists(doctype, {fieldname: candidate}):
			return candidate

	frappe.throw(
		frappe._("Could not allocate a free {0} for {1} after {2} attempts.").format(
			fieldname, doctype, _MAX_ID_PROBES
		)
	)


def sync_all_numeric_id_series() -> dict:
	"""after_migrate hook — heal every numeric-ID counter in one pass.

	Runs after any migrate, which is also what follows a dump restore, so a
	refreshed site does not have to discover each lagging counter through a
	failed transaction.
	"""
	healed = {}
	for sequence in NUMERIC_ID_TARGETS:
		try:
			healed[sequence] = sync_numeric_id_series(sequence)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"sync_numeric_id_series failed for {sequence}"
			)
	frappe.db.commit()
	return healed


def next_prefixed_code(namespace: str, prefix: str, digits: int) -> str:
	prefix = str(prefix or "").strip().upper()
	key = f"{namespace}::{prefix}::"
	_ensure_series(key)
	return f"{prefix}{getseries(key, digits)}"


def next_scoped_number(namespace: str, scope: str) -> int:
	digest = hashlib.sha256(str(scope).encode("utf-8")).hexdigest()
	key = f"{namespace}::{digest}"
	_ensure_series(key)
	return int(getseries(key, 9))
