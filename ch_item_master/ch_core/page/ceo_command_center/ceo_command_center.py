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
	cache_key = f"ceo_cc|{company or 'all'}|{store or 'all'}|{period}"
	cached = frappe.cache.get_value(cache_key)
	if cached:
		return json.loads(cached)

	data = {
		"summary": _get_summary_kpis(ctx),
		"conversion": _get_conversion_data(ctx),
		"attach": _get_attach_data(ctx),
		"leakage": _get_leakage_data(ctx),
		"repairs": _get_repair_data(ctx),
		"stores": _get_store_rankings(ctx),
		"alerts": _get_active_alerts(),
		"hourly_trend": _get_hourly_trend(ctx),
	}

	frappe.cache.set_value(cache_key, json.dumps(data, default=str), expires_in_sec=300)
	return data


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
	""".format(**ctx), ctx, as_dict=1)
	kpis["revenue"] = flt(revenue[0].total) if revenue else 0

	# Invoice Count
	inv_count = frappe.db.sql("""
		SELECT COUNT(*) as cnt
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
	""".format(**ctx), ctx, as_dict=1)
	kpis["invoice_count"] = cint(inv_count[0].cnt) if inv_count else 0

	# Avg Bill Value
	kpis["avg_bill_value"] = flt(kpis["revenue"] / kpis["invoice_count"]) if kpis["invoice_count"] else 0

	# Footfall (Tokens)
	tokens = frappe.db.sql("""
		SELECT COUNT(*) as cnt
		FROM `tabPOS Kiosk Token` t
		WHERE t.creation BETWEEN %(from_date)s AND %(to_date)s
	""", ctx, as_dict=1)
	kpis["footfall"] = cint(tokens[0].cnt) if tokens else 0

	# Conversion %
	kpis["conversion_pct"] = flt(kpis["invoice_count"] / kpis["footfall"] * 100, 1) if kpis["footfall"] else 0

	return kpis


def _get_conversion_data(ctx):
	"""Conversion funnel data: tokens → invoices."""
	hourly = frappe.db.sql("""
		SELECT HOUR(t.creation) as hr, COUNT(*) as tokens
		FROM `tabPOS Kiosk Token` t
		WHERE DATE(t.creation) BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY HOUR(t.creation)
		ORDER BY hr
	""", ctx, as_dict=1)

	invoices_hourly = frappe.db.sql("""
		SELECT HOUR(si.posting_time) as hr, COUNT(*) as invoices
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{sql_and}
		GROUP BY HOUR(si.posting_time)
		ORDER BY hr
	""".format(**ctx), ctx, as_dict=1)

	inv_map = {r.hr: r.invoices for r in invoices_hourly}
	return [{"hour": r.hr, "tokens": r.tokens, "invoices": inv_map.get(r.hr, 0)} for r in hourly]


def _get_attach_data(ctx):
	"""Attach rate data from CH Attach Log."""
	if not frappe.db.table_exists("tabCH Attach Log"):
		return {"warranty_rate": 0, "accessory_rate": 0, "vas_rate": 0}

	data = frappe.db.sql("""
		SELECT
			al.attach_type,
			COUNT(*) as total,
			SUM(CASE WHEN al.action = 'Accepted' THEN 1 ELSE 0 END) as accepted
		FROM `tabCH Attach Log` al
		WHERE al.offered_at BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY al.attach_type
	""", ctx, as_dict=1)

	result = {}
	for row in data:
		rate = flt(row.accepted / row.total * 100, 1) if row.total else 0
		result[f"{row.attach_type.lower()}_rate"] = rate
		result[f"{row.attach_type.lower()}_total"] = row.total
		result[f"{row.attach_type.lower()}_accepted"] = row.accepted

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
	""".format(**ctx), ctx, as_dict=1)
	if disc:
		leakage["discount_override_pct"] = flt(disc[0].disc_items / disc[0].total_items * 100, 1) if disc[0].total_items else 0
	else:
		leakage["discount_override_pct"] = 0

	return leakage


def _get_repair_data(ctx):
	"""Repair/GoFix metrics: open SRs, avg TAT, SLA breaches."""
	repairs = {}

	open_srs = frappe.db.count("Service Request",
		filters={"decision": ["in", ["Accepted", "In Service"]], "docstatus": 1})
	repairs["open_service_requests"] = cint(open_srs)

	# Avg TAT for completed in period
	tat = frappe.db.sql("""
		SELECT AVG(TIMESTAMPDIFF(HOUR, sr.received_datetime, sr.modified)) as avg_tat
		FROM `tabService Request` sr
		WHERE sr.decision IN ('Completed', 'Delivered')
			AND sr.modified BETWEEN %(from_date)s AND %(to_date)s
	""", ctx, as_dict=1)
	repairs["avg_tat_hours"] = flt(tat[0].avg_tat, 1) if tat and tat[0].avg_tat else 0

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
	""".format(**ctx), ctx, as_dict=1)

	return {
		"top_5": stores[:5],
		"bottom_5": list(reversed(stores[-5:])) if len(stores) > 5 else [],
		"all": stores
	}


def _get_active_alerts():
	"""Fetch active alerts from CH CEO Alert if it exists."""
	if not frappe.db.table_exists("tabCH CEO Alert"):
		return []

	return frappe.get_all("CH CEO Alert",
		filters={"is_active": 1},
		fields=["name", "alert_type", "severity", "message", "store", "creation"],
		order_by="creation desc",
		limit=20)


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
	""".format(**ctx), ctx, as_dict=1)
