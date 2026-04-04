# Copyright (c) 2025, GoStack and contributors
# CEO Command Center - Backend API
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import (
	nowdate, now_datetime, add_days, get_first_day, get_last_day,
	getdate, flt, cint, time_diff_in_hours
)
from datetime import datetime, timedelta
import json


def get_context(context):
	context.no_cache = 1


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _parse_period(period):
	"""Return (from_date, to_date) for the given period string."""
	today = getdate(nowdate())
	if period == "today":
		return today, today
	elif period == "wtd":
		weekday = today.weekday()
		return today - timedelta(days=weekday), today
	elif period == "mtd":
		return get_first_day(today), today
	elif period == "this_month":
		return get_first_day(today), today
	elif period == "last_month":
		last_month_end = get_first_day(today) - timedelta(days=1)
		return get_first_day(last_month_end), last_month_end
	elif period == "last_quarter":
		quarter_month = ((today.month - 1) // 3) * 3 + 1
		q_start = today.replace(month=quarter_month, day=1)
		prev_q_end = q_start - timedelta(days=1)
		prev_q_month = ((prev_q_end.month - 1) // 3) * 3 + 1
		return prev_q_end.replace(month=prev_q_month, day=1), prev_q_end
	elif period == "1_year":
		return today - timedelta(days=365), today
	elif period == "qtd":
		quarter_month = ((today.month - 1) // 3) * 3 + 1
		return today.replace(month=quarter_month, day=1), today
	elif period == "ytd":
		return today.replace(month=1, day=1), today
	elif ":" in str(period):
		parts = str(period).split(":")
		return getdate(parts[0]), getdate(parts[1])
	return get_first_day(today), today


def _build_conditions(company=None, store=None, period="today"):
	"""Build reusable SQL condition fragments."""
	from_date, to_date = _parse_period(period)
	conditions = {"from_date": from_date, "to_date": to_date}

	sql_parts = []
	if company:
		sql_parts.append("AND si.company = %(company)s")
		conditions["company"] = company
	if store:
		if frappe.db.exists("POS Profile", store):
			sql_parts.append("AND si.pos_profile = %(store)s")
		else:
			sql_parts.append("AND si.pos_profile IN (SELECT name FROM `tabPOS Profile` WHERE warehouse = %(store)s)")
		conditions["store"] = store

	conditions["sql_and"] = " ".join(sql_parts)
	return conditions


def _build_prev_conditions(company=None, store=None, period="today"):
	"""Build conditions for the previous comparable period (for WoW/MoM trends)."""
	from_date, to_date = _parse_period(period)
	span = (to_date - from_date).days + 1

	prev_to = from_date - timedelta(days=1)
	prev_from = prev_to - timedelta(days=span - 1)

	conditions = {"from_date": prev_from, "to_date": prev_to}
	sql_parts = []
	if company:
		sql_parts.append("AND si.company = %(company)s")
		conditions["company"] = company
	if store:
		if frappe.db.exists("POS Profile", store):
			sql_parts.append("AND si.pos_profile = %(store)s")
		else:
			sql_parts.append("AND si.pos_profile IN (SELECT name FROM `tabPOS Profile` WHERE warehouse = %(store)s)")
		conditions["store"] = store

	conditions["sql_and"] = " ".join(sql_parts)
	return conditions


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_command_center_data(company=None, store=None, period="today"):
	"""Unified CEO Command Center data endpoint."""
	frappe.has_permission("Sales Invoice", throw=True)

	ctx = _build_conditions(company, store, period)
	prev_ctx = _build_prev_conditions(company, store, period)
	cache_key = "ceo_cc|{}|{}|{}".format(company or "all", store or "all", period)
	cached = frappe.cache.get_value(cache_key)
	if cached:
		return json.loads(cached)

	data = {
		"summary": _get_summary_kpis(ctx),
		"prev_summary": _get_summary_kpis(prev_ctx),
		"conversion": _get_conversion_data(ctx),
		"attach": _get_attach_data(ctx),
		"leakage": _get_leakage_data(ctx),
		"repairs": _get_repair_data(ctx),
		"stores": _get_store_rankings(ctx),
		"scorecards": _get_store_scorecards(company=company, period=period),
		"alerts": _get_active_alerts(company=company),
		"hourly_trend": _get_hourly_trend(ctx),
		"prev_hourly_trend": _get_hourly_trend(prev_ctx),
		"inventory": _get_inventory_summary(ctx),
		"warranty_claims": _get_warranty_claims_summary(ctx),
		"buyback": _get_buyback_summary(ctx),
	}

	# AI analysis runs on all collected data
	data["ai_insights"] = _get_ai_insights(data, ctx)

	frappe.cache.set_value(cache_key, json.dumps(data, default=str), expires_in_sec=300)
	return data


@frappe.whitelist()
def get_store_drilldown(store, company=None, period="today"):
	"""Detailed KPI snapshot for a single POS Profile (store)."""
	frappe.has_permission("Sales Invoice", throw=True)
	if not store:
		frappe.throw(_("Store is required"))

	ctx = _build_conditions(company=company, store=store, period=period)

	# Revenue / invoice counters
	bill_data = frappe.db.sql("""
		SELECT
			COALESCE(SUM(si.grand_total), 0) AS revenue,
			COUNT(*) AS invoice_count,
			AVG(si.grand_total) AS avg_bill_value
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032

	# Walk-ins/footfall from POS Kiosk Token
	footfall_data = frappe.db.sql("""
		SELECT COUNT(*) AS footfall
		FROM `tabPOS Kiosk Token` t
		WHERE DATE(t.creation) BETWEEN %(from_date)s AND %(to_date)s
			AND t.pos_profile = %(store)s
	""", ctx, as_dict=1)

	# Discount leakage (% of discounted lines)
	discount_data = frappe.db.sql("""
		SELECT
			COUNT(*) AS total_items,
			SUM(CASE WHEN sii.discount_percentage > 0 THEN 1 ELSE 0 END) AS discounted_items
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032

	# Active repair load for this store (via warehouse -> source_warehouse)
	repair_filters = {
		"decision": ["in", ["Accepted", "In Service"]],
		"docstatus": 1,
	}
	warehouse = frappe.db.get_value("POS Profile", store, "warehouse")
	if warehouse:
		repair_filters["source_warehouse"] = warehouse

	open_repairs = frappe.db.count("Service Request", filters=repair_filters)

	revenue = (bill_data[0].revenue if bill_data else 0) or 0
	invoice_count = (bill_data[0].invoice_count if bill_data else 0) or 0
	avg_bill_value = (bill_data[0].avg_bill_value if bill_data else 0) or 0
	footfall = (footfall_data[0].footfall if footfall_data else 0) or 0

	total_items = (discount_data[0].total_items if discount_data else 0) or 0
	discounted_items = (discount_data[0].discounted_items if discount_data else 0) or 0

	attach = _get_attach_data(ctx)

	return {
		"store": store,
		"company": company,
		"period": period,
		"summary": {
			"revenue": flt(revenue),
			"invoice_count": cint(invoice_count),
			"avg_bill_value": flt(avg_bill_value),
			"footfall": cint(footfall),
			"conversion_pct": flt(invoice_count / footfall * 100, 1) if footfall else 0,
			"warranty_attach_pct": flt(attach.get("warranty_rate", 0)),
			"accessory_attach_pct": flt(attach.get("accessory_rate", 0)),
			"discount_override_pct": flt(discounted_items / total_items * 100, 1) if total_items else 0,
			"open_repairs": cint(open_repairs),
		},
	}


# ---------------------------------------------------------------------------
# KPI helpers
# ---------------------------------------------------------------------------

def _get_summary_kpis(ctx):
	"""Top-level KPI summary cards."""
	kpis = {}

	# Revenue
	revenue = frappe.db.sql("""
		SELECT COALESCE(SUM(si.grand_total), 0) as total
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032
	kpis["revenue"] = flt(revenue[0].total) if revenue else 0

	# Invoice Count
	inv_count = frappe.db.sql("""
		SELECT COUNT(*) as cnt
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032
	kpis["invoice_count"] = cint(inv_count[0].cnt) if inv_count else 0

	# Avg Bill Value
	kpis["avg_bill_value"] = flt(kpis["revenue"] / kpis["invoice_count"]) if kpis["invoice_count"] else 0

	# Footfall (Tokens)
	token_sql = """
		SELECT COUNT(*) as cnt
		FROM `tabPOS Kiosk Token` t
		WHERE DATE(t.creation) BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		token_sql += " AND t.company = %(company)s"
	tokens = frappe.db.sql(token_sql, ctx, as_dict=1)
	kpis["footfall"] = cint(tokens[0].cnt) if tokens else 0

	# Conversion %
	kpis["conversion_pct"] = flt(kpis["invoice_count"] / kpis["footfall"] * 100, 1) if kpis["footfall"] else 0

	return kpis


def _get_conversion_data(ctx):
	"""Conversion funnel data: tokens → invoices."""
	token_sql = """
		SELECT HOUR(t.creation) as hr, COUNT(*) as tokens
		FROM `tabPOS Kiosk Token` t
		WHERE DATE(t.creation) BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		token_sql += " AND t.company = %(company)s"
	token_sql += " GROUP BY HOUR(t.creation) ORDER BY hr"
	hourly = frappe.db.sql(token_sql, ctx, as_dict=1)

	invoices_hourly = frappe.db.sql("""
		SELECT HOUR(si.posting_time) as hr, COUNT(*) as invoices
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
		GROUP BY HOUR(si.posting_time)
		ORDER BY hr
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032

	inv_map = {r.hr: r.invoices for r in invoices_hourly}
	return [{"hour": r.hr, "tokens": r.tokens, "invoices": inv_map.get(r.hr, 0)} for r in hourly]


def _get_attach_data(ctx):
	"""Attach rates: warranty/VAS from CH Sold Plan, accessory from Sales Invoice Item groups."""
	result = {"warranty_rate": 0, "accessory_rate": 0, "vas_rate": 0}

	# Total device invoices in period (denominator for attach rate)
	inv_sql = """
		SELECT COUNT(DISTINCT si.name) as cnt
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
	""".format(**ctx)
	total_inv = frappe.db.sql(inv_sql, ctx, as_dict=1)
	denominator = cint(total_inv[0].cnt) if total_inv else 0
	if not denominator:
		return result

	# Warranty + VAS attach from CH Sold Plan
	if frappe.db.table_exists("CH Sold Plan"):
		plan_sql = """
			SELECT sp.plan_type, COUNT(DISTINCT sp.sales_invoice) as cnt
			FROM `tabCH Sold Plan` sp
			WHERE sp.docstatus = 1
				AND DATE(sp.creation) BETWEEN %(from_date)s AND %(to_date)s
				AND sp.sales_invoice IS NOT NULL AND sp.sales_invoice != ''
		"""
		if ctx.get("company"):
			plan_sql += " AND sp.company = %(company)s"
		plan_sql += " GROUP BY sp.plan_type"
		plans = frappe.db.sql(plan_sql, ctx, as_dict=1)
		for row in plans:
			pt = (row.plan_type or "").lower()
			rate = flt(row.cnt / denominator * 100, 1)
			if "warranty" in pt or "protection" in pt:
				result["warranty_rate"] += rate
			elif "vas" in pt or "value added" in pt:
				result["vas_rate"] += rate

	# Accessory attach: invoices that contain an Accessories item group line
	acc_sql = """
		SELECT COUNT(DISTINCT sii.parent) as cnt
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			AND sii.item_group = 'Accessories'
			{sql_and}
	""".format(**ctx)
	acc = frappe.db.sql(acc_sql, ctx, as_dict=1)
	if acc:
		result["accessory_rate"] = flt(cint(acc[0].cnt) / denominator * 100, 1)

	return result


def _get_leakage_data(ctx):
	"""Leakage metrics: discount overrides, no-attach, spare variance."""
	leakage = {}

	# Discount overrides
	disc = frappe.db.sql("""
		SELECT
			COUNT(CASE WHEN sii.discount_percentage > 0 THEN 1 END) as disc_items,
			COUNT(*) as total_items
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032
	if disc:
		leakage["discount_override_pct"] = flt(disc[0].disc_items / disc[0].total_items * 100, 1) if disc[0].total_items else 0
	else:
		leakage["discount_override_pct"] = 0

	return leakage


def _get_repair_data(ctx):
	"""Repair/GoFix metrics: open SRs, avg TAT, SLA breaches, QC stats."""
	repairs = {}

	sr_filters = {"decision": ["in", ["Accepted", "In Service"]], "docstatus": 1}
	if ctx.get("company"):
		sr_filters["company"] = ctx["company"]

	open_srs = frappe.db.count("Service Request", filters=sr_filters)
	repairs["open_service_requests"] = cint(open_srs)

	# Avg TAT for completed in period
	tat_sql = """
		SELECT AVG(TIMESTAMPDIFF(HOUR, sr.received_datetime, sr.modified)) as avg_tat
		FROM `tabService Request` sr
		WHERE sr.decision IN ('Completed', 'Delivered')
			AND DATE(sr.modified) BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		tat_sql += " AND sr.company = %(company)s"
	tat = frappe.db.sql(tat_sql, ctx, as_dict=1)
	repairs["avg_tat_hours"] = flt(tat[0].avg_tat, 1) if tat and tat[0].avg_tat else 0

	# SLA breach count for open SRs
	breach_count = 0
	try:
		from gofix.gofix_services.doctype.gofix_sla_rule.gofix_sla_rule import get_sla_rule

		breach_filters = {"decision": ["in", ["Accepted", "In Service"]], "docstatus": 1}
		if ctx.get("company"):
			breach_filters["company"] = ctx["company"]

		active_srs = frappe.get_all("Service Request",
			filters=breach_filters,
			fields=["issue_category", "priority", "received_datetime",
					"warranty_status", "warranty_plan", "company"])
		now = now_datetime()
		for sr in active_srs:
			if not sr.received_datetime:
				continue
			rule = get_sla_rule(
				sr.issue_category, sr.priority,
				company=sr.company,
				warranty_plan=sr.warranty_plan,
				warranty_status=sr.warranty_status,
			)
			if rule and time_diff_in_hours(now, sr.received_datetime) > (rule.target_hours or 0):
				breach_count += 1
	except Exception:
		pass
	repairs["sla_breached"] = breach_count

	# QC stats from warranty claims
	qc_sql = """
		SELECT
			SUM(CASE WHEN wc.intake_qc_status = 'Passed' THEN 1 ELSE 0 END) as qc_passed,
			SUM(CASE WHEN wc.intake_qc_status = 'Failed' THEN 1 ELSE 0 END) as qc_failed,
			SUM(CASE WHEN wc.intake_qc_status = 'Not Repairable' THEN 1 ELSE 0 END) as qc_not_repairable
		FROM `tabCH Warranty Claim` wc
		WHERE wc.docstatus < 2
			AND wc.claim_date BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		qc_sql += " AND wc.company = %(company)s"
	if frappe.db.table_exists("CH Warranty Claim"):
		qc = frappe.db.sql(qc_sql, ctx, as_dict=1)
		repairs["qc_passed"] = cint(qc[0].qc_passed) if qc else 0
		repairs["qc_failed"] = cint(qc[0].qc_failed) if qc else 0
		repairs["qc_not_repairable"] = cint(qc[0].qc_not_repairable) if qc else 0
	else:
		repairs["qc_passed"] = repairs["qc_failed"] = repairs["qc_not_repairable"] = 0

	return repairs


def _get_store_rankings(ctx):
	"""Top 5 and bottom 5 stores by revenue."""
	stores = frappe.db.sql("""
		SELECT
			si.pos_profile as store,
			SUM(si.grand_total) as revenue,
			COUNT(*) as invoices
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.pos_profile IS NOT NULL AND si.pos_profile != ''
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
		GROUP BY si.pos_profile
		ORDER BY revenue DESC
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032

	return {
		"top_5": stores[:5],
		"bottom_5": list(reversed(stores[-5:])) if len(stores) > 5 else [],
		"all": stores
	}


def _normalized(actual, target):
	"""Normalize actual against target and cap at 1.0."""
	actual = flt(actual)
	target = flt(target)
	if target <= 0:
		return 0
	return min(actual / target, 1)


def _get_scorecard_settings():
	"""Return scorecard weights and thresholds from settings or defaults."""
	defaults = frappe._dict(
		conversion_weight=25,
		gocare_attach_weight=20,
		revenue_vs_target_weight=20,
		discount_control_weight=15,
		repair_tat_weight=10,
		session_discipline_weight=10,
		low_conversion_threshold=40,
		low_gocare_attach_threshold=25,
		high_discount_threshold=8,
	)
	try:
		settings = frappe.get_cached_doc("CH CEO Dashboard Settings")
		for key in defaults.keys():
			if settings.get(key) is None:
				settings.set(key, defaults.get(key))
		return settings
	except Exception:
		return defaults


@frappe.whitelist()
def get_store_scorecards(company=None, period="today"):
	"""Return weighted health scorecards for POS stores."""
	frappe.has_permission("Sales Invoice", throw=True)
	return _get_store_scorecards(company=company, period=period)


def _get_store_scorecards(company=None, period="today"):
	"""Internal helper to calculate store scorecards."""
	settings = _get_scorecard_settings()
	stores = frappe.get_all("POS Profile", filters={"disabled": 0}, pluck="name")
	if not stores:
		return []

	rows = []
	for store in stores:
		try:
			d = get_store_drilldown(store=store, company=company, period=period)
			s = d.get("summary", {})
			rows.append({
				"store": store,
				"summary": s,
			})
		except Exception:
			continue

	if not rows:
		return []

	revenue_target = flt(sum(flt(r["summary"].get("revenue", 0)) for r in rows) / len(rows)) or 1
	conv_target = cint(settings.get("low_conversion_threshold")) or 40
	attach_target = cint(settings.get("low_gocare_attach_threshold")) or 25
	disc_target = cint(settings.get("high_discount_threshold")) or 8

	total_weight = (
		(cint(settings.get("conversion_weight")) or 25)
		+ (cint(settings.get("gocare_attach_weight")) or 20)
		+ (cint(settings.get("revenue_vs_target_weight")) or 20)
		+ (cint(settings.get("discount_control_weight")) or 15)
		+ (cint(settings.get("repair_tat_weight")) or 10)
		+ (cint(settings.get("session_discipline_weight")) or 10)
	)

	result = []
	for row in rows:
		s = row["summary"]
		conv_norm = _normalized(s.get("conversion_pct", 0), conv_target)
		attach_norm = _normalized(s.get("warranty_attach_pct", 0), attach_target)
		revenue_norm = _normalized(s.get("revenue", 0), revenue_target)
		discount_norm = max(0, 1 - (flt(s.get("discount_override_pct", 0)) / max(disc_target, 1)))
		repair_norm = 1 if cint(s.get("open_repairs", 0)) <= 5 else flt(5 / max(cint(s.get("open_repairs", 0)), 1))
		session_norm = 1

		score_weighted = (
			conv_norm * (cint(settings.get("conversion_weight")) or 25)
			+ attach_norm * (cint(settings.get("gocare_attach_weight")) or 20)
			+ revenue_norm * (cint(settings.get("revenue_vs_target_weight")) or 20)
			+ discount_norm * (cint(settings.get("discount_control_weight")) or 15)
			+ repair_norm * (cint(settings.get("repair_tat_weight")) or 10)
			+ session_norm * (cint(settings.get("session_discipline_weight")) or 10)
		)

		result.append({
			"store": row["store"],
			"score": flt((score_weighted / max(total_weight, 1)) * 100, 1),
			"summary": s,
			"components": {
				"conversion_norm": flt(conv_norm, 3),
				"attach_norm": flt(attach_norm, 3),
				"revenue_norm": flt(revenue_norm, 3),
				"discount_norm": flt(discount_norm, 3),
				"repair_norm": flt(repair_norm, 3),
				"session_norm": flt(session_norm, 3),
			},
		})

	result.sort(key=lambda d: d.get("score", 0), reverse=True)
	return result


def _get_active_alerts(company=None):
	"""Fetch active alerts from CH CEO Alert if it exists."""
	if not frappe.db.table_exists("CH CEO Alert"):
		return []

	filters = {"is_active": 1}
	alert_sql = """
		SELECT a.name, a.alert_type, a.severity, a.message, a.store, a.creation
		FROM `tabCH CEO Alert` a
		WHERE a.is_active = 1
	"""
	params = {}
	if company:
		alert_sql += """
			AND (a.store IS NULL OR a.store = ''
				OR a.store IN (SELECT pp.name FROM `tabPOS Profile` pp WHERE pp.company = %(company)s))
		"""
		params["company"] = company
	alert_sql += " ORDER BY a.creation DESC LIMIT 20"

	return frappe.db.sql(alert_sql, params, as_dict=1)


def _get_hourly_trend(ctx):
	"""Hourly revenue trend for the current period."""
	return frappe.db.sql("""
		SELECT
			HOUR(si.posting_time) as hour,
			SUM(si.grand_total) as revenue,
			COUNT(*) as invoices
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
		GROUP BY HOUR(si.posting_time)
		ORDER BY hour
	""".format(**ctx), ctx, as_dict=1)  # noqa: UP032


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def _get_inventory_summary(ctx):
	"""Stock value, quantity, dead stock, and in-transit counts."""
	result = {
		"total_stock_value": 0,
		"total_stock_qty": 0,
		"dead_stock_items": 0,
		"slow_moving_items": 0,
		"in_transit_count": 0,
	}

	# Get thresholds from settings
	settings = _get_scorecard_settings()
	dead_days = cint(settings.get("dead_stock_days")) or 90
	slow_days = cint(settings.get("slow_moving_days")) or 45

	# Total stock value + qty from Bin (filtered by company via warehouse)
	stock_sql = """
		SELECT
			COALESCE(SUM(b.stock_value), 0) as stock_value,
			COALESCE(SUM(b.actual_qty), 0) as stock_qty
		FROM `tabBin` b
	"""
	params = {}
	if ctx.get("company"):
		stock_sql += """
			JOIN `tabWarehouse` w ON w.name = b.warehouse
			WHERE w.company = %(company)s
		"""
		params["company"] = ctx["company"]
	stock_data = frappe.db.sql(stock_sql, params, as_dict=1)
	if stock_data:
		result["total_stock_value"] = flt(stock_data[0].stock_value)
		result["total_stock_qty"] = flt(stock_data[0].stock_qty)

	# Dead stock: items in Bin with qty > 0 but no SLE in last N days
	dead_sql = """
		SELECT COUNT(DISTINCT b.item_code) as cnt
		FROM `tabBin` b
		{join_clause}
		WHERE b.actual_qty > 0
			AND b.item_code NOT IN (
				SELECT DISTINCT sle.item_code
				FROM `tabStock Ledger Entry` sle
				WHERE sle.posting_date >= %(cutoff_date)s
					AND sle.is_cancelled = 0
			)
	"""
	dead_params = {"cutoff_date": add_days(nowdate(), -dead_days)}
	join_clause = ""
	if ctx.get("company"):
		join_clause = "JOIN `tabWarehouse` w ON w.name = b.warehouse AND w.company = %(company)s"
		dead_params["company"] = ctx["company"]
	dead_data = frappe.db.sql(dead_sql.format(join_clause=join_clause), dead_params, as_dict=1)
	result["dead_stock_items"] = cint(dead_data[0].cnt) if dead_data else 0

	# Slow moving: items with SLE activity but only 1-2 transactions in last N days
	slow_sql = """
		SELECT COUNT(*) as cnt FROM (
			SELECT sle.item_code, COUNT(*) as txns
			FROM `tabStock Ledger Entry` sle
			{join_clause}
			WHERE sle.posting_date >= %(cutoff_date)s
				AND sle.is_cancelled = 0
			GROUP BY sle.item_code
			HAVING txns <= 2
		) slow
	"""
	slow_params = {"cutoff_date": add_days(nowdate(), -slow_days)}
	join_clause = ""
	if ctx.get("company"):
		join_clause = "JOIN `tabWarehouse` w ON w.name = sle.warehouse AND w.company = %(company)s"
		slow_params["company"] = ctx["company"]
	slow_data = frappe.db.sql(slow_sql.format(join_clause=join_clause), slow_params, as_dict=1)
	result["slow_moving_items"] = cint(slow_data[0].cnt) if slow_data else 0

	# In-transit: Draft Material Transfer Stock Entries
	transit_filters = {
		"stock_entry_type": "Material Transfer",
		"docstatus": 0,
	}
	if ctx.get("company"):
		transit_filters["company"] = ctx["company"]
	result["in_transit_count"] = frappe.db.count("Stock Entry", filters=transit_filters)

	return result


# ---------------------------------------------------------------------------
# Warranty Claims
# ---------------------------------------------------------------------------

def _get_warranty_claims_summary(ctx):
	"""Claims by status, cost splits, plan utilization."""
	result = {
		"by_status": [],
		"cost_splits": {"gogizmo": 0, "gofix": 0, "customer": 0},
		"total_claims": 0,
		"plan_utilization_pct": 0,
		"by_coverage": [],
	}

	if not frappe.db.table_exists("CH Warranty Claim"):
		return result

	# Claims by status
	status_sql = """
		SELECT wc.claim_status, COUNT(*) as cnt
		FROM `tabCH Warranty Claim` wc
		WHERE wc.docstatus < 2
			AND wc.claim_date BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		status_sql += " AND wc.company = %(company)s"
	status_sql += " GROUP BY wc.claim_status ORDER BY cnt DESC"
	result["by_status"] = frappe.db.sql(status_sql, ctx, as_dict=1)
	result["total_claims"] = sum(r.cnt for r in result["by_status"])

	# Cost splits
	cost_sql = """
		SELECT
			COALESCE(SUM(wc.gogizmo_share), 0) as gogizmo,
			COALESCE(SUM(wc.gofix_share), 0) as gofix,
			COALESCE(SUM(wc.customer_share), 0) as customer
		FROM `tabCH Warranty Claim` wc
		WHERE wc.docstatus < 2
			AND wc.claim_date BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		cost_sql += " AND wc.company = %(company)s"
	costs = frappe.db.sql(cost_sql, ctx, as_dict=1)
	if costs:
		result["cost_splits"] = {
			"gogizmo": flt(costs[0].gogizmo),
			"gofix": flt(costs[0].gofix),
			"customer": flt(costs[0].customer),
		}

	# By coverage type
	cov_sql = """
		SELECT wc.coverage_type, COUNT(*) as cnt
		FROM `tabCH Warranty Claim` wc
		WHERE wc.docstatus < 2
			AND wc.claim_date BETWEEN %(from_date)s AND %(to_date)s
			AND wc.coverage_type IS NOT NULL AND wc.coverage_type != ''
	"""
	if ctx.get("company"):
		cov_sql += " AND wc.company = %(company)s"
	cov_sql += " GROUP BY wc.coverage_type ORDER BY cnt DESC"
	result["by_coverage"] = frappe.db.sql(cov_sql, ctx, as_dict=1)

	# Plan utilization: active sold plans vs claims filed
	if frappe.db.table_exists("CH Sold Plan"):
		active_plans = frappe.db.count("CH Sold Plan",
			filters={"docstatus": 1, "status": ["in", ["Active", "Claimed"]]})
		claimed = frappe.db.count("CH Sold Plan",
			filters={"docstatus": 1, "status": "Claimed"})
		result["plan_utilization_pct"] = flt(claimed / active_plans * 100, 1) if active_plans else 0

	return result


# ---------------------------------------------------------------------------
# Buyback
# ---------------------------------------------------------------------------

def _get_buyback_summary(ctx):
	"""Buyback orders by status, settlement type, pricing."""
	result = {
		"by_status": [],
		"by_settlement_type": [],
		"total_orders": 0,
		"total_value": 0,
		"avg_order_value": 0,
	}

	if not frappe.db.table_exists("Buyback Order"):
		return result

	# By status
	status_sql = """
		SELECT bo.status, COUNT(*) as cnt, COALESCE(SUM(bo.final_price), 0) as value
		FROM `tabBuyback Order` bo
		WHERE bo.docstatus < 2
			AND DATE(bo.creation) BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		status_sql += " AND bo.company = %(company)s"
	status_sql += " GROUP BY bo.status ORDER BY cnt DESC"
	result["by_status"] = frappe.db.sql(status_sql, ctx, as_dict=1)
	result["total_orders"] = sum(r.cnt for r in result["by_status"])
	result["total_value"] = flt(sum(r.value for r in result["by_status"]))
	result["avg_order_value"] = flt(result["total_value"] / result["total_orders"]) if result["total_orders"] else 0

	# By settlement type
	settle_sql = """
		SELECT bo.settlement_type, COUNT(*) as cnt, COALESCE(SUM(bo.final_price), 0) as value
		FROM `tabBuyback Order` bo
		WHERE bo.docstatus < 2
			AND DATE(bo.creation) BETWEEN %(from_date)s AND %(to_date)s
	"""
	if ctx.get("company"):
		settle_sql += " AND bo.company = %(company)s"
	settle_sql += " GROUP BY bo.settlement_type ORDER BY cnt DESC"
	result["by_settlement_type"] = frappe.db.sql(settle_sql, ctx, as_dict=1)

	return result


# ---------------------------------------------------------------------------
# AI Insights Engine
# ---------------------------------------------------------------------------

def _get_ai_insights(data, ctx):
	"""Analyse all dashboard data and return prioritised AI recommendations.

	Categories:
	  revenue    – Revenue & growth patterns
	  conversion – Walk-in to sale conversion
	  inventory  – Dead/slow stock, stock-out risk
	  service    – SLA, TAT, QC problems
	  warranty   – Claim trends, cost exposure
	  buyback    – Buyback & exchange trends
	  leakage    – Discount abuse, margin erosion
	  staffing   – Peak-hour resource gaps

	Each insight: {category, severity (critical/warning/info/opportunity),
	               title, detail, action, metric_value, benchmark}
	"""
	insights = []

	s = data.get("summary") or {}
	prev = data.get("prev_summary") or {}
	inv = data.get("inventory") or {}
	rep = data.get("repairs") or {}
	wc = data.get("warranty_claims") or {}
	bb = data.get("buyback") or {}
	attach = data.get("attach") or {}
	leakage = data.get("leakage") or {}
	stores = data.get("stores") or {}
	hourly = data.get("hourly_trend") or []
	conv_data = data.get("conversion") or []

	settings = _get_scorecard_settings()

	# ── Revenue Insights ─────────────────────────────────────────────────
	revenue = flt(s.get("revenue", 0))
	prev_revenue = flt(prev.get("revenue", 0))
	if prev_revenue > 0:
		rev_change = (revenue - prev_revenue) / prev_revenue * 100
		if rev_change < -15:
			insights.append({
				"category": "revenue",
				"severity": "critical",
				"title": "Revenue declining sharply",
				"detail": "Revenue is down {:.1f}% vs previous period (₹{:,.0f} → ₹{:,.0f}). Investigate store-level performance and footfall drop.".format(
					rev_change, prev_revenue, revenue),
				"action": "Review bottom 3 stores, check if footfall dropped or conversion fell. Consider flash promotions.",
				"metric_value": "{:.1f}%".format(rev_change),
				"benchmark": "< -5% triggers review",
			})
		elif rev_change < -5:
			insights.append({
				"category": "revenue",
				"severity": "warning",
				"title": "Revenue trending down",
				"detail": "Revenue is down {:.1f}% vs previous period.".format(rev_change),
				"action": "Monitor for 2 more days. If trend continues, activate targeted offers in underperforming stores.",
				"metric_value": "{:.1f}%".format(rev_change),
				"benchmark": "Target: flat or positive growth",
			})
		elif rev_change > 20:
			insights.append({
				"category": "revenue",
				"severity": "opportunity",
				"title": "Strong revenue momentum",
				"detail": "Revenue is up {:.1f}% vs previous period. Capitalise on this momentum.".format(rev_change),
				"action": "Identify winning categories and stores. Double down on what's working — extend offers, increase inventory for hot SKUs.",
				"metric_value": "+{:.1f}%".format(rev_change),
				"benchmark": "Above 10% is excellent",
			})

	# Avg bill value insight
	abv = flt(s.get("avg_bill_value", 0))
	prev_abv = flt(prev.get("avg_bill_value", 0))
	if prev_abv > 0 and abv > 0:
		abv_change = (abv - prev_abv) / prev_abv * 100
		if abv_change < -10:
			insights.append({
				"category": "revenue",
				"severity": "warning",
				"title": "Average bill value dropping",
				"detail": "ABV dropped {:.1f}% (₹{:,.0f} → ₹{:,.0f}). Customers buying cheaper or fewer items per transaction.".format(
					abv_change, prev_abv, abv),
				"action": "Push combo offers, accessory bundles, and warranty attach. Train staff on upselling techniques.",
				"metric_value": "₹{:,.0f}".format(abv),
				"benchmark": "Previous: ₹{:,.0f}".format(prev_abv),
			})

	# ── Conversion Insights ──────────────────────────────────────────────
	conv_pct = flt(s.get("conversion_pct", 0))
	conv_threshold = cint(settings.get("low_conversion_threshold")) or 40
	if conv_pct > 0 and conv_pct < conv_threshold:
		insights.append({
			"category": "conversion",
			"severity": "critical",
			"title": "Low walk-in conversion",
			"detail": "Only {:.1f}% of walk-ins converted to sales (target: {}%). Potential revenue leaking at the door.".format(
				conv_pct, conv_threshold),
			"action": "Review store greeting SOP, check wait times, ensure adequate staffing during peak hours. Consider mystery shopper audit.",
			"metric_value": "{:.1f}%".format(conv_pct),
			"benchmark": "Target: ≥{}%".format(conv_threshold),
		})
	elif conv_pct >= conv_threshold * 1.3:
		insights.append({
			"category": "conversion",
			"severity": "opportunity",
			"title": "Excellent conversion rate",
			"detail": "Conversion at {:.1f}% — well above target. Store teams are performing well.".format(conv_pct),
			"action": "Recognise top-converting stores. Share best practices across network.",
			"metric_value": "{:.1f}%".format(conv_pct),
			"benchmark": "Target: ≥{}%".format(conv_threshold),
		})

	# Peak hour analysis
	if hourly:
		peak = max(hourly, key=lambda h: flt(h.get("revenue", 0)))
		dead_hours = [h for h in hourly if flt(h.get("revenue", 0)) < flt(peak.get("revenue", 0)) * 0.15 and h.get("hour", 0) >= 10]
		if dead_hours and flt(peak.get("revenue", 0)) > 0:
			dead_labels = ", ".join("{}:00".format(h["hour"]) for h in dead_hours[:3])
			insights.append({
				"category": "staffing",
				"severity": "info",
				"title": "Dead hours identified",
				"detail": "Very low activity at {}. Peak is at {}:00 (₹{:,.0f}).".format(
					dead_labels, peak["hour"], flt(peak["revenue"])),
				"action": "Optimise staff scheduling: reduce headcount during dead hours, reinforce during peak. Consider time-limited offers to boost dead-hour traffic.",
				"metric_value": "{} dead hrs".format(len(dead_hours)),
				"benchmark": "Peak revenue: ₹{:,.0f}".format(flt(peak["revenue"])),
			})

		# Conversion drop analysis per hour
		if conv_data:
			conv_map = {c.get("hour"): c for c in conv_data}
			low_conv_hours = []
			for c in conv_data:
				tokens = cint(c.get("tokens", 0))
				invoices = cint(c.get("invoices", 0))
				if tokens >= 5 and tokens > 0:
					hr_conv = invoices / tokens * 100
					if hr_conv < conv_threshold * 0.6:
						low_conv_hours.append({"hour": c["hour"], "rate": hr_conv, "tokens": tokens})
			if low_conv_hours:
				worst = min(low_conv_hours, key=lambda x: x["rate"])
				insights.append({
					"category": "conversion",
					"severity": "warning",
					"title": "Conversion crashes at specific hours",
					"detail": "At {}:00, only {:.0f}% of {} walk-ins converted. Staff may be overwhelmed or absent.".format(
						worst["hour"], worst["rate"], worst["tokens"]),
					"action": "Check staffing at {}:00. Add floater staff during high-traffic low-conversion windows.".format(worst["hour"]),
					"metric_value": "{:.0f}%".format(worst["rate"]),
					"benchmark": "Target: ≥{}%".format(conv_threshold),
				})

	# ── Inventory Insights ───────────────────────────────────────────────
	dead_stock = cint(inv.get("dead_stock_items", 0))
	slow_moving = cint(inv.get("slow_moving_items", 0))
	stock_value = flt(inv.get("total_stock_value", 0))

	if dead_stock > 0:
		severity = "critical" if dead_stock > 50 else "warning"
		dead_pct = flt(dead_stock / max(dead_stock + slow_moving + 1, 1) * 100, 1)
		insights.append({
			"category": "inventory",
			"severity": severity,
			"title": "{} dead stock items blocking capital".format(dead_stock),
			"detail": "{} items have zero movement in {} days. This capital is locked and depreciating.".format(
				dead_stock, cint(settings.get("dead_stock_days")) or 90),
			"action": "Run clearance sale with 20-30% markdown. Move dead stock to online channel. Consider buyback/exchange credit on these items.",
			"metric_value": "{} items".format(dead_stock),
			"benchmark": "Target: < 10 dead stock items",
		})

	if slow_moving > 20:
		insights.append({
			"category": "inventory",
			"severity": "info",
			"title": "{} slow-moving items need attention".format(slow_moving),
			"detail": "These items have very low turnover in the last {} days.".format(
				cint(settings.get("slow_moving_days")) or 45),
			"action": "Bundle slow movers with fast sellers. Position them near checkout. Consider promotional pricing before they become dead stock.",
			"metric_value": "{} items".format(slow_moving),
			"benchmark": "Watch list — may become dead stock",
		})

	in_transit = cint(inv.get("in_transit_count", 0))
	if in_transit > 10:
		insights.append({
			"category": "inventory",
			"severity": "info",
			"title": "{} stock transfers in transit".format(in_transit),
			"detail": "Multiple pending material transfers. Ensure receiving warehouses acknowledge promptly.",
			"action": "Follow up on transfers older than 48hrs. Check for stuck/lost shipments.",
			"metric_value": "{} transfers".format(in_transit),
			"benchmark": "Normal: < 5 pending",
		})

	# ── Service & Repair Insights ────────────────────────────────────────
	sla_breached = cint(rep.get("sla_breached", 0))
	open_srs = cint(rep.get("open_service_requests", 0))
	avg_tat = flt(rep.get("avg_tat_hours", 0))
	qc_failed = cint(rep.get("qc_failed", 0))
	qc_passed = cint(rep.get("qc_passed", 0))

	if sla_breached > 0:
		severity = "critical" if sla_breached > 5 else "warning"
		insights.append({
			"category": "service",
			"severity": severity,
			"title": "{} service requests breached SLA".format(sla_breached),
			"detail": "These devices are overdue for resolution. Customer satisfaction and brand reputation at risk.".format(),
			"action": "Escalate breached SRs immediately. Prioritise oldest-first. Offer complimentary accessory to affected customers as goodwill.",
			"metric_value": "{} breached".format(sla_breached),
			"benchmark": "Target: 0 breaches",
		})

	if avg_tat > 72:
		insights.append({
			"category": "service",
			"severity": "warning",
			"title": "Repair TAT too high ({:.0f} hrs)".format(avg_tat),
			"detail": "Average turnaround is {:.0f} hours ({:.1f} days). Customers expect same-day or next-day for simple repairs.".format(
				avg_tat, avg_tat / 24),
			"action": "Audit spare parts availability. Check if technicians are overloaded. Consider outsourcing overflow to partner workshop.",
			"metric_value": "{:.0f} hrs".format(avg_tat),
			"benchmark": "Target: < 48 hrs",
		})

	if qc_failed > 0 and (qc_passed + qc_failed) > 0:
		fail_rate = qc_failed / (qc_passed + qc_failed) * 100
		if fail_rate > 15:
			insights.append({
				"category": "service",
				"severity": "critical",
				"title": "High QC failure rate ({:.0f}%)".format(fail_rate),
				"detail": "{} out of {} inspected devices failed QC. Either repair quality is poor or intake assessment is inaccurate.".format(
					qc_failed, qc_passed + qc_failed),
				"action": "Retrain technicians on QC standards. Review intake condition grading process. Consider mandatory photo evidence on intake.",
				"metric_value": "{:.0f}%".format(fail_rate),
				"benchmark": "Target: < 10% fail rate",
			})

	if open_srs > 50:
		insights.append({
			"category": "service",
			"severity": "warning",
			"title": "Service backlog building up ({})".format(open_srs),
			"detail": "{} open service requests in pipeline. Risk of cascading SLA breaches.".format(open_srs),
			"action": "Activate weekend overtime for technicians. Defer new intake bookings if capacity is full. Communicate proactively with waiting customers.",
			"metric_value": "{} open SRs".format(open_srs),
			"benchmark": "Healthy: < 30 open",
		})

	# ── Warranty / VAS Insights ──────────────────────────────────────────
	total_claims = cint(wc.get("total_claims", 0))
	cost_splits = wc.get("cost_splits") or {}
	gogizmo_cost = flt(cost_splits.get("gogizmo", 0))
	plan_util = flt(wc.get("plan_utilization_pct", 0))

	if gogizmo_cost > 0 and revenue > 0:
		warranty_exposure = gogizmo_cost / revenue * 100
		if warranty_exposure > 3:
			insights.append({
				"category": "warranty",
				"severity": "warning",
				"title": "Warranty cost exposure at {:.1f}% of revenue".format(warranty_exposure),
				"detail": "GoGizmo is bearing ₹{:,.0f} in warranty costs this period. This erodes margins.".format(gogizmo_cost),
				"action": "Review claim approval thresholds. Push extended warranty plans to reduce future exposure. Negotiate better terms with OEM.",
				"metric_value": "₹{:,.0f}".format(gogizmo_cost),
				"benchmark": "Target: < 2% of revenue",
			})

	attach_threshold = cint(settings.get("low_gocare_attach_threshold")) or 25
	warranty_rate = flt(attach.get("warranty_rate", 0))
	vas_rate = flt(attach.get("vas_rate", 0))
	accessory_rate = flt(attach.get("accessory_rate", 0))

	if warranty_rate < attach_threshold and warranty_rate > 0:
		insights.append({
			"category": "warranty",
			"severity": "warning",
			"title": "Low warranty attach rate ({:.0f}%)".format(warranty_rate),
			"detail": "Only {:.0f}% of eligible items got warranty attached. Missing ₹ revenue on every sale.".format(warranty_rate),
			"action": "Mandatory warranty pitch before checkout. Display claim success stories at counter. Offer first-month-free trial on plans.",
			"metric_value": "{:.0f}%".format(warranty_rate),
			"benchmark": "Target: ≥{}%".format(attach_threshold),
		})

	if accessory_rate < 15 and accessory_rate >= 0:
		insights.append({
			"category": "leakage",
			"severity": "info",
			"title": "Accessory attach rate only {:.0f}%".format(accessory_rate),
			"detail": "Most customers leave without buying accessories. Major ABV opportunity missed.".format(),
			"action": "Create device + accessory combo offers. Place accessories near billing counter. Train staff to recommend top 3 accessories per device.",
			"metric_value": "{:.0f}%".format(accessory_rate),
			"benchmark": "Industry benchmark: 25-35%",
		})

	if plan_util > 40:
		insights.append({
			"category": "warranty",
			"severity": "info",
			"title": "High plan utilization ({:.0f}%)".format(plan_util),
			"detail": "{:.0f}% of sold warranty plans have been claimed. Need to check if pricing covers costs.".format(plan_util),
			"action": "Review warranty plan pricing vs actual claim costs. Consider adjusting premiums or deductibles for high-claim categories.",
			"metric_value": "{:.0f}%".format(plan_util),
			"benchmark": "Healthy: 15-30% utilization",
		})

	# ── Buyback Insights ─────────────────────────────────────────────────
	bb_total = cint(bb.get("total_orders", 0))
	bb_value = flt(bb.get("total_value", 0))
	bb_by_type = bb.get("by_settlement_type") or []

	exchange_count = sum(r.cnt for r in bb_by_type if r.get("settlement_type") == "Exchange")
	buyback_count = sum(r.cnt for r in bb_by_type if r.get("settlement_type") == "Buyback")

	if bb_total > 0 and exchange_count > 0:
		exchange_pct = exchange_count / bb_total * 100
		if exchange_pct > 60:
			insights.append({
				"category": "buyback",
				"severity": "opportunity",
				"title": "Exchange driving {:.0f}% of buyback volume".format(exchange_pct),
				"detail": "{} out of {} buyback orders are exchanges. Each exchange = new device sale + higher ABV.".format(
					exchange_count, bb_total),
				"action": "Promote exchange offers prominently. Train staff to pitch exchange on every repair intake (device upgrade opportunity).",
				"metric_value": "{}  exchanges".format(exchange_count),
				"benchmark": "Exchange-heavy mix is positive for revenue",
			})

	if buyback_count > 0 and revenue > 0:
		buyback_rev_pct = bb_value / revenue * 100
		if buyback_rev_pct > 5:
			insights.append({
				"category": "buyback",
				"severity": "info",
				"title": "Buyback program at {:.1f}% of revenue".format(buyback_rev_pct),
				"detail": "₹{:,.0f} in buyback payouts this period across {} orders.".format(bb_value, bb_total),
				"action": "Ensure refurb pipeline is absorbing buyback inventory. Track margin on refurb resales vs buyback cost.",
				"metric_value": "₹{:,.0f}".format(bb_value),
				"benchmark": "Monitor refurb resale margin",
			})

	# ── Discount / Leakage Insights ──────────────────────────────────────
	disc_pct = flt(leakage.get("discount_override_pct", 0))
	disc_threshold = cint(settings.get("high_discount_threshold")) or 8

	if disc_pct > disc_threshold:
		insights.append({
			"category": "leakage",
			"severity": "critical",
			"title": "Discount override rate too high ({:.1f}%)".format(disc_pct),
			"detail": "{:.1f}% of line items have discounts applied. This exceeds the {}% policy limit.".format(
				disc_pct, disc_threshold),
			"action": "Audit discount reasons. Check for unapproved overrides. Tighten manager approval workflow. Review if discount reasons are being gamed.",
			"metric_value": "{:.1f}%".format(disc_pct),
			"benchmark": "Policy limit: {}%".format(disc_threshold),
		})

	# ── Store Performance Insights ───────────────────────────────────────
	top_5 = stores.get("top_5") or []
	bottom_5 = stores.get("bottom_5") or []
	all_stores = stores.get("all") or []

	if len(all_stores) >= 3:
		revenues = [flt(st.get("revenue", 0)) for st in all_stores if flt(st.get("revenue", 0)) > 0]
		if revenues:
			avg_store_rev = sum(revenues) / len(revenues)
			underperformers = [st for st in all_stores
				if flt(st.get("revenue", 0)) < avg_store_rev * 0.4 and flt(st.get("revenue", 0)) > 0]
			if underperformers:
				names = ", ".join(st.get("store", "?") for st in underperformers[:3])
				insights.append({
					"category": "revenue",
					"severity": "warning",
					"title": "{} stores significantly below average".format(len(underperformers)),
					"detail": "{} at <40% of average store revenue (₹{:,.0f}). Investigate local market, staffing, inventory.".format(
						names, avg_store_rev),
					"action": "Conduct store visit. Check local competition, footfall trends, and staff morale. Consider pop-up marketing or influencer tie-ups.",
					"metric_value": "{} stores".format(len(underperformers)),
					"benchmark": "Avg store revenue: ₹{:,.0f}".format(avg_store_rev),
				})

	# ── Predictive / Forward-Looking ─────────────────────────────────────
	# Revenue run-rate projection
	from_date = ctx.get("from_date")
	to_date = ctx.get("to_date")
	if from_date and to_date:
		today = getdate(nowdate())
		period_days = (getdate(to_date) - getdate(from_date)).days + 1
		elapsed_days = (today - getdate(from_date)).days + 1
		if elapsed_days > 0 and period_days > elapsed_days and revenue > 0:
			daily_run_rate = revenue / elapsed_days
			projected = daily_run_rate * period_days
			remaining = projected - revenue
			insights.append({
				"category": "revenue",
				"severity": "info",
				"title": "Revenue projection: ₹{:,.0f}".format(projected),
				"detail": "At current run rate of ₹{:,.0f}/day, you'll close the period at ₹{:,.0f}. {} days remaining.".format(
					daily_run_rate, projected, period_days - elapsed_days),
				"action": "Use this projection to evaluate if monthly/quarterly targets are on track. Adjust push intensity accordingly.",
				"metric_value": "₹{:,.0f}/day".format(daily_run_rate),
				"benchmark": "Projected total: ₹{:,.0f}".format(projected),
			})

	# Sort: critical first, then warning, opportunity, info
	severity_order = {"critical": 0, "warning": 1, "opportunity": 2, "info": 3}
	insights.sort(key=lambda x: severity_order.get(x.get("severity", "info"), 9))

	return insights
