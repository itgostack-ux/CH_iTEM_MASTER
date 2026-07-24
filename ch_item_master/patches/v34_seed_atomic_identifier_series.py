import re

import frappe

from ch_item_master.id_sequences import NUMERIC_ID_SERIES


_NUMERIC_FIELDS = {
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


def _seed_series(key: str, current: int) -> None:
	frappe.db.sql(
		"""
		INSERT INTO `tabSeries` (`name`, `current`) VALUES (%s, %s)
		ON DUPLICATE KEY UPDATE `current` = GREATEST(`current`, VALUES(`current`))
		""",
		(key, int(current or 0)),
	)


def execute():
	for sequence, (doctype, fieldname) in _NUMERIC_FIELDS.items():
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, fieldname):
			continue
		maximum = frappe.db.sql(
			f"SELECT COALESCE(MAX(`{fieldname}`), 0) FROM `tab{doctype}`"
		)[0][0]
		_seed_series(NUMERIC_ID_SERIES[sequence], maximum)

	if not frappe.db.table_exists("CH Sub Category") or not frappe.db.table_exists("Item"):
		return
	prefixes = frappe.get_all(
		"CH Sub Category",
		filters={"prefix": ("not in", ("", None))},
		pluck="prefix",
	)
	for raw_prefix in sorted(set(prefixes)):
		prefix = str(raw_prefix or "").strip().upper()
		if not prefix:
			continue
		item_codes = frappe.get_all(
			"Item",
			filters={"item_code": ("like", f"{prefix}%")},
			pluck="item_code",
		)
		maximum = max(
			(
				int(match.group(1))
				for code in item_codes
				if (match := re.fullmatch(rf"{re.escape(prefix)}(\d{{6}})", code or ""))
			),
			default=0,
		)
		_seed_series(f"CH-ITEM-CODE::{prefix}::", maximum)
