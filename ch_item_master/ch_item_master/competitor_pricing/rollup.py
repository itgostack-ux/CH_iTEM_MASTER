# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Reduce many competitor snapshots into one band per item.

The band is deliberately built from the **latest snapshot per competitor**,
not from every snapshot ever taken. Two weeks of daily captures from one
competitor would otherwise outvote a single capture from four others, and the
median would describe our collection schedule rather than the market.

Median rather than mean, for the same reason the analysis that started this
work refused to quote the highest advertised figure: one promotional outlier
should not move the reference the pricing team plans against.
"""

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime

from ch_item_master.config import get_int_setting

#: Preferred price per snapshot, best evidence first. An evaluated quote beats
#: an advertised maximum because it survived a condition questionnaire.
_PRICE_PREFERENCE = ("evaluated_quote", "verified_payout", "advertised_max")


def _median(values: list) -> float:
	if not values:
		return 0.0
	ordered = sorted(values)
	mid = len(ordered) // 2
	if len(ordered) % 2:
		return flt(ordered[mid])
	return flt((ordered[mid - 1] + ordered[mid]) / 2.0)


def _best_price(row: dict) -> float:
	for field in _PRICE_PREFERENCE:
		value = flt(row.get(field))
		if value:
			return value
	return 0.0


def _latest_per_competitor(item_code: str, condition_profile: str) -> list:
	"""One row per competitor — the most recent priced snapshot it gave us."""
	rows = frappe.get_all(
		"CH Competitor Price Snapshot",
		filters={
			"item_code": item_code,
			"condition_profile": condition_profile,
			"fetch_status": "Success",
		},
		fields=[
			"competitor", "captured_at", "advertised_max", "evaluated_quote",
			"verified_payout", "refurb_selling_price",
		],
		order_by="captured_at desc",
		limit_page_length=0,
	)

	seen = {}
	for row in rows:
		if row["competitor"] in seen:
			continue
		if not _best_price(row):
			continue
		seen[row["competitor"]] = row
	return list(seen.values())


def recompute_rollup_for(item_code: str, condition_profile: str) -> str | None:
	"""Rebuild one item × profile band. Returns the rollup name, or None when
	there is no usable evidence and any existing band was withdrawn."""
	if not item_code or not condition_profile:
		return None

	rows = _latest_per_competitor(item_code, condition_profile)
	name = frappe.db.get_value(
		"CH Competitor Price Rollup",
		{"item_code": item_code, "condition_profile": condition_profile},
		"name",
	)

	if not rows:
		# No evidence left — withdraw the band rather than leave a stale one
		# standing. A missing number is honest; an old one pretending to be
		# current is not.
		if name:
			frappe.delete_doc("CH Competitor Price Rollup", name,
			                  ignore_permissions=True, force=True)
		return None

	prices = [_best_price(row) for row in rows]
	refurb = [flt(row.get("refurb_selling_price")) for row in rows if flt(row.get("refurb_selling_price"))]
	captures = [row["captured_at"] for row in rows if row.get("captured_at")]

	max_age_days = get_int_setting("competitor_data_max_age_days", 7, minimum=1)
	cutoff = add_to_date(now_datetime(), days=-max_age_days)
	latest = max(captures) if captures else None

	values = {
		"item_code": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name") or item_code,
		"condition_profile": condition_profile,
		"quote_count": len(prices),
		"competitor_count": len({row["competitor"] for row in rows}),
		"competitor_list": ", ".join(sorted({row["competitor"] for row in rows}))[:500],
		"market_low": min(prices),
		"market_median": _median(prices),
		"market_high": max(prices),
		"market_refurb_median": _median(refurb),
		"latest_captured_at": latest,
		"oldest_captured_at": min(captures) if captures else None,
		"is_stale": 1 if (not latest or latest < cutoff) else 0,
		"computed_at": now_datetime(),
	}

	if name:
		doc = frappe.get_doc("CH Competitor Price Rollup", name)
		doc.update(values)
	else:
		doc = frappe.new_doc("CH Competitor Price Rollup")
		doc.update(values)
		doc.name = f"{item_code}::{condition_profile}"

	doc.flags.ignore_permissions = True
	doc.save()
	return doc.name


def recompute_all_rollups(batch_limit: int = None) -> dict:
	"""Scheduled rebuild across every item × profile that has evidence."""
	batch_limit = cint(batch_limit) or get_int_setting("scheduler_batch_limit", 500, minimum=1)

	pairs = frappe.db.sql(
		"""
		SELECT DISTINCT item_code, condition_profile
		FROM `tabCH Competitor Price Snapshot`
		WHERE fetch_status = 'Success'
		ORDER BY item_code
		LIMIT %s
		""",
		(batch_limit,),
		as_dict=True,
	)

	rebuilt = 0
	for pair in pairs:
		try:
			recompute_rollup_for(pair["item_code"], pair["condition_profile"])
			rebuilt += 1
		except Exception:
			frappe.log_error(
				title=f"Rollup failed: {pair['item_code']} / {pair['condition_profile']}",
				message=frappe.get_traceback(),
			)
		if rebuilt % 100 == 0:
			frappe.db.commit()

	frappe.db.commit()
	return {"pairs": len(pairs), "rebuilt": rebuilt}


def mark_stale_rollups() -> int:
	"""Flip the stale flag on bands that aged out since the last rebuild.

	Runs far more cheaply than a full recompute, so it can be scheduled often
	enough that the planner never shows an aged band as current.
	"""
	max_age_days = get_int_setting("competitor_data_max_age_days", 7, minimum=1)
	cutoff = add_to_date(now_datetime(), days=-max_age_days)

	stale = frappe.get_all(
		"CH Competitor Price Rollup",
		filters={"is_stale": 0, "latest_captured_at": ("<", cutoff)},
		pluck="name",
		limit_page_length=0,
	)
	for name in stale:
		frappe.db.set_value("CH Competitor Price Rollup", name, "is_stale", 1, update_modified=False)

	frappe.db.commit()
	return len(stale)
