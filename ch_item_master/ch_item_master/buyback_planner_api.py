# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Buyback Price Planner — one view for planning buy prices against the market.

The planner answers a question the Ready Reckoner cannot: *which models should
we be pricing, and at what?* It puts three things on one row —

* what we currently pay (the Buyback Price Master grade × warranty matrix)
* what the market pays (the collected competitor band)
* how much the model actually matters (12-month sales, our best available
  proxy for what will come back as a trade-in)

— and then lets the pricing team stage new prices and push them into the
existing CH Price Upload Batch maker/checker. The planner itself never writes
a price. That distinction is the whole point: collection is evidence, the
planner is a proposal, and the batch approval is the decision.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from ch_item_master.config import get_int_setting, get_role_setting, is_privileged_user

#: The matrix cell a competitor's headline figure is comparable to: best
#: grade, freshest warranty band. Comparing their "up to" price against our
#: Grade C out-of-warranty number would make us look permanently uncompetitive.
BENCHMARK_FIELD = "a_grade_iw_0_3"

#: Buyback Price Master columns the planner can stage changes for.
PLANNABLE_FIELDS = (
	"a_grade_iw_0_3", "b_grade_iw_0_3", "c_grade_iw_0_3",
	"a_grade_iw_0_6", "b_grade_iw_0_6", "c_grade_iw_0_6", "d_grade_iw_0_6",
	"a_grade_iw_6_11", "b_grade_iw_6_11", "c_grade_iw_6_11", "d_grade_iw_6_11",
	"a_grade_oow_11", "b_grade_oow_11", "c_grade_oow_11", "d_grade_oow_11",
	"scrap_price", "phone_dead_price")

#: How far our price may drift from the market median before the planner says
#: something. Expressed as a fraction of the median.
_BELOW_MARKET_TOLERANCE = 0.10
_ABOVE_MARKET_TOLERANCE = 0.05


def _require_planner_access():
	if is_privileged_user():
		return
	allowed = get_role_setting("buyback_planner_roles")
	if not allowed.intersection(frappe.get_roles(frappe.session.user)):
		frappe.throw(
			_("You are not permitted to use the Buyback Price Planner."),
			frappe.PermissionError)


def _sales_rank_map(item_codes: list, months: int = 12) -> dict:
	"""Units sold per model over the window. A phone we sold is a phone that
	comes back, so this is the closest thing we have to expected intake until
	real buyback history accumulates."""
	if not item_codes:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT sii.item_code, SUM(sii.qty) AS qty
		FROM `tabSales Invoice Item` sii
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent AND si.docstatus = 1
		WHERE sii.item_code IN %(items)s
		  AND si.posting_date >= DATE_SUB(CURDATE(), INTERVAL %(months)s MONTH)
		GROUP BY sii.item_code
		""",
		{"items": tuple(item_codes), "months": cint(months)},
		as_dict=True)
	return {row["item_code"]: flt(row["qty"]) for row in rows}


def _site_prices(item_codes: list, condition_profile: str) -> dict:
	"""Latest price per competitor per item, for the side-by-side columns.

	The band on its own tells you the market moved; it does not tell you *who*
	moved. On an iPhone 13 Pro Max, Cashkr at ₹47,000 against Cashify at
	₹34,030 is a ₹12,970 spread on one phone — which competitor sits where is
	the whole analysis, and a median hides it.

	Self-joined against the latest capture per (item, competitor) so a
	fortnight of daily captures from one site cannot outweigh a single capture
	from another.
	"""
	if not item_codes:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT s.item_code, s.competitor, s.captured_at, s.source_url,
		       s.advertised_max, s.evaluated_quote, s.verified_payout
		FROM `tabCH Competitor Price Snapshot` s
		INNER JOIN (
			SELECT item_code, competitor, MAX(captured_at) AS latest
			FROM `tabCH Competitor Price Snapshot`
			WHERE item_code IN %(items)s
			  AND condition_profile = %(profile)s
			  AND fetch_status = 'Success'
			GROUP BY item_code, competitor
		) newest
		   ON newest.item_code = s.item_code
		  AND newest.competitor = s.competitor
		  AND newest.latest = s.captured_at
		""",
		{"items": tuple(item_codes), "profile": condition_profile},
		as_dict=True)

	by_item = {}
	for row in rows:
		# Same preference order the rollup uses: a condition-evaluated quote
		# outranks a headline "up to" figure.
		price = flt(row.evaluated_quote) or flt(row.verified_payout) or flt(row.advertised_max)
		if not price:
			continue
		by_item.setdefault(row.item_code, {})[row.competitor] = {
			"price": price,
			"captured_at": row.captured_at,
			"url": row.source_url,
		}
	return by_item


def _insights(our_price: float, rollup: dict, has_master: bool) -> list:
	"""Short, actionable flags. Each one implies a specific next action, which
	is why there is no generic 'review' chip."""
	flags = []

	if not has_master:
		flags.append({"key": "no_price", "label": _("No buy price set"), "tone": "critical"})

	if not rollup:
		flags.append({"key": "no_market", "label": _("No market data"), "tone": "neutral"})
		return flags

	if rollup.get("is_stale"):
		flags.append({"key": "stale", "label": _("Market data stale"), "tone": "warning"})

	median = flt(rollup.get("market_median"))
	if median and our_price:
		delta = (our_price - median) / median
		if delta < -_BELOW_MARKET_TOLERANCE:
			flags.append({
				"key": "below_market",
				"label": _("{0}% below market").format(abs(round(delta * 100))),
				"tone": "warning",
			})
		elif delta > _ABOVE_MARKET_TOLERANCE:
			flags.append({
				"key": "above_market",
				"label": _("{0}% above market").format(round(delta * 100)),
				"tone": "critical",
			})

	# The margin trap from the original analysis: buying close to what the
	# market resells for leaves nothing for refurbishment, warranty or profit.
	refurb = flt(rollup.get("market_refurb_median"))
	if refurb and our_price and our_price > refurb * 0.85:
		flags.append({
			"key": "thin_margin",
			"label": _("Within 15% of resale price"),
			"tone": "critical",
		})

	if cint(rollup.get("competitor_count")) == 1:
		flags.append({"key": "single_source", "label": _("Single source"), "tone": "neutral"})

	return flags


@frappe.whitelist()
def get_planner_rows(
	item_group: str = "Mobiles",
	condition_profile: str = "Good",
	search: str = "",
	coverage: str = "",
	page: int = 1,
	page_length: int = 50,
	sort_by: str = "sales") -> dict:
	"""One page of planner rows, ranked by whichever signal matters right now.

	``coverage`` narrows to the work worth doing: ``missing_price`` for models
	we buy but have never priced, ``has_market`` for models we can actually
	plan against today.
	"""
	_require_planner_access()

	page = max(cint(page), 1)
	page_length = min(max(cint(page_length) or 50, 1),
	                  get_int_setting("ready_reckoner_page_limit", 200, minimum=1))

	conditions = ["i.disabled = 0"]
	params = {"profile": condition_profile, "bench": BENCHMARK_FIELD}

	if item_group:
		conditions.append("i.item_group = %(item_group)s")
		params["item_group"] = item_group
	if search:
		conditions.append("(i.name LIKE %(search)s OR i.item_name LIKE %(search)s)")
		params["search"] = f"%{search}%"

	if coverage == "missing_price":
		conditions.append("bpm.name IS NULL")
	elif coverage == "has_price":
		conditions.append("bpm.name IS NOT NULL")
	elif coverage == "has_market":
		conditions.append("r.name IS NOT NULL")
	elif coverage == "no_market":
		conditions.append("r.name IS NULL")

	where = " AND ".join(conditions)

	# Sales volume is joined as a correlated aggregate rather than a subquery
	# per row so the ORDER BY can use it without a second pass.
	base = f"""
		FROM `tabItem` i
		LEFT JOIN `tabBuyback Price Master` bpm
		       ON bpm.item_code = i.name AND bpm.is_active = 1
		LEFT JOIN `tabCH Competitor Price Rollup` r
		       ON r.item_code = i.name AND r.condition_profile = %(profile)s
		WHERE {where}
	"""

	total = frappe.db.sql(f"SELECT COUNT(*) {base}", params)[0][0]

	order = {
		"sales": "sales_qty DESC, i.item_name ASC",
		"gap": "gap_pct ASC, sales_qty DESC",
		"name": "i.item_name ASC",
	}.get(sort_by, "sales_qty DESC, i.item_name ASC")

	params["limit"] = page_length
	params["offset"] = (page - 1) * page_length

	rows = frappe.db.sql(
		f"""
		SELECT
			i.name AS item_code,
			i.item_name,
			i.brand,
			bpm.name AS price_master,
			bpm.{BENCHMARK_FIELD} AS our_price,
			bpm.scrap_price,
			bpm.phone_dead_price,
			bpm.current_market_price,
			r.name AS rollup,
			r.market_low, r.market_median, r.market_high,
			r.market_refurb_median, r.quote_count, r.competitor_count,
			r.competitor_list, r.latest_captured_at, r.is_stale,
			COALESCE((
				SELECT SUM(sii.qty)
				FROM `tabSales Invoice Item` sii
				INNER JOIN `tabSales Invoice` si
				        ON si.name = sii.parent AND si.docstatus = 1
				WHERE sii.item_code = i.name
				  AND si.posting_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
			), 0) AS sales_qty,
			CASE
				WHEN IFNULL(r.market_median, 0) = 0 THEN NULL
				ELSE (IFNULL(bpm.{BENCHMARK_FIELD}, 0) - r.market_median) / r.market_median
			END AS gap_pct
		{base}
		ORDER BY {order}
		LIMIT %(limit)s OFFSET %(offset)s
		""",
		params,
		as_dict=True)

	site_prices = _site_prices([r["item_code"] for r in rows], condition_profile)

	for row in rows:
		rollup = {
			"market_low": row.get("market_low"),
			"market_median": row.get("market_median"),
			"market_high": row.get("market_high"),
			"market_refurb_median": row.get("market_refurb_median"),
			"competitor_count": row.get("competitor_count"),
			"is_stale": row.get("is_stale"),
		} if row.get("rollup") else None

		row["insights"] = _insights(
			flt(row.get("our_price")), rollup, bool(row.get("price_master"))
		)
		row["suggested_price"] = _suggest(row)
		row["sites"] = site_prices.get(row["item_code"], {})

	return {
		"rows": rows,
		"total": total,
		"page": page,
		"page_length": page_length,
		"benchmark_field": BENCHMARK_FIELD,
		"condition_profile": condition_profile,
		# One column per competitor we actually collect from, so the grid can
		# lay out side by side without the client guessing the set.
		"competitors": frappe.get_all(
			"CH Competitor Source",
			filters={"disabled": 0},
			pluck="name",
			order_by="competitor_name asc"),
	}


def _suggest(row: dict) -> float:
	"""A starting number for the pricing team, never an applied price.

	Anchored on the median rather than the high: the analysis behind this
	feature showed the advertised maximum is a marketing ceiling, and matching
	it is how you buy at a loss.
	"""
	median = flt(row.get("market_median"))
	if not median or cint(row.get("is_stale")):
		return 0.0

	refurb = flt(row.get("market_refurb_median"))
	suggestion = median

	# Never propose a buy price that leaves no room between us and the resale
	# price the market itself achieves.
	if refurb:
		suggestion = min(suggestion, refurb * 0.75)

	return flt(round(suggestion, -1))


@frappe.whitelist()
def get_planner_summary(item_group: str = "Mobiles", condition_profile: str = "Good") -> dict:
	"""Headline coverage numbers — what the team is actually up against."""
	_require_planner_access()

	row = frappe.db.sql(
		"""
		SELECT
			COUNT(*) AS models,
			SUM(CASE WHEN bpm.name IS NOT NULL THEN 1 ELSE 0 END) AS priced,
			SUM(CASE WHEN r.name IS NOT NULL THEN 1 ELSE 0 END) AS with_market,
			SUM(CASE WHEN r.name IS NOT NULL AND r.is_stale = 1 THEN 1 ELSE 0 END) AS stale
		FROM `tabItem` i
		LEFT JOIN `tabBuyback Price Master` bpm
		       ON bpm.item_code = i.name AND bpm.is_active = 1
		LEFT JOIN `tabCH Competitor Price Rollup` r
		       ON r.item_code = i.name AND r.condition_profile = %(profile)s
		WHERE i.disabled = 0 AND i.item_group = %(item_group)s
		""",
		{"item_group": item_group, "profile": condition_profile},
		as_dict=True)[0]

	row["links"] = frappe.db.count("CH Competitor Item Link")
	row["sources"] = frappe.db.count("CH Competitor Source", {"disabled": 0})
	row["snapshots_7d"] = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabCH Competitor Price Snapshot`
		   WHERE captured_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)"""
	)[0][0]
	return row


@frappe.whitelist(methods=["POST"])
def create_planner_batch(changes, reason: str = "", company: str = "") -> dict:
	"""Stage planned prices as a Draft CH Price Upload Batch.

	``changes`` is a list of ``{item_code, field, new_value}``. Rows whose
	value already matches are dropped rather than written as no-op changes, so
	the batch an approver sees contains only real decisions.

	This reuses the same row shape ``create_price_change_batch`` produces —
	including the convention that a buyback row stores the Buyback Price
	Master **field name** in ``channel``, which is what the apply step reads.
	"""
	_require_planner_access()
	frappe.has_permission("CH Price Upload Batch", "create", throw=True)

	from ch_item_master.ch_item_master.ready_reckoner_api import (
		_BUYBACK_FIELD_LABELS,
		_enrich_batch_items)

	if isinstance(changes, str):
		changes = json.loads(changes)
	if not isinstance(changes, list) or not changes:
		frappe.throw(_("No planned prices were submitted."), title=_("Nothing To Stage"))

	row_limit = get_int_setting("ready_reckoner_batch_item_limit", 500, minimum=1)
	if len(changes) > row_limit:
		frappe.throw(
			_("A price batch can contain at most {0} changes. Stage them in smaller groups.")
			.format(row_limit),
			frappe.ValidationError)

	reason = (reason or "").strip()
	if not reason:
		frappe.throw(
			_("Give a reason — it is what the approver reads to judge the batch."),
			title=_("Reason Required"))

	# One read per item rather than one per change: a planner submission
	# typically touches several grades of the same model.
	item_codes = sorted({c.get("item_code") for c in changes if c.get("item_code")})
	if not item_codes:
		frappe.throw(_("No item codes in the submitted changes."), title=_("Nothing To Stage"))

	existing = {
		row["item_code"]: row
		for row in frappe.get_all(
			"Buyback Price Master",
			filters={"item_code": ("in", item_codes), "is_active": 1},
			fields=["item_code", *PLANNABLE_FIELDS])
	}

	batch_items = []
	skipped = 0
	for change in changes:
		item_code = change.get("item_code")
		field = change.get("field")
		if not item_code or field not in PLANNABLE_FIELDS:
			skipped += 1
			continue

		new_value = flt(change.get("new_value"))
		old_value = flt((existing.get(item_code) or {}).get(field))
		if new_value == old_value:
			skipped += 1
			continue

		batch_items.append({
			"item_code": item_code,
			"channel": field,
			"change_type": "Buyback Price",
			"field_label": _BUYBACK_FIELD_LABELS.get(field, field),
			"old_value": str(old_value),
			"new_value": str(new_value),
			"reason": reason,
		})

	if not batch_items:
		frappe.throw(
			_("Every submitted price already matches the current value — nothing to approve."),
			title=_("No Changes"))

	batch = frappe.new_doc("CH Price Upload Batch")
	batch.title = _("Buyback Planner — {0} models, {1} changes").format(
		len(item_codes), len(batch_items)
	)
	batch.uploaded_by = frappe.session.user
	batch.upload_date = nowdate()
	batch.status = "Draft"
	batch.notes = _("Raised from the Buyback Price Planner.\nReason: {0}").format(reason)
	if company:
		batch.company = company

	for row in batch_items:
		batch.append("items", row)

	_enrich_batch_items(batch, set(item_codes))
	batch.insert()

	return {
		"batch_name": batch.name,
		"total_changes": len(batch_items),
		"skipped": skipped,
	}
