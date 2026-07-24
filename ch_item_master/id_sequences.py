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
