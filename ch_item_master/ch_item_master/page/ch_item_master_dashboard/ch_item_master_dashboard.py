# Copyright (c) 2026, GoStack and contributors
# Item Master Dashboard — Backend API

"""
Serves all dashboard metrics, alerts, and AI-based insights
in a single API call for the dashboard page.
"""

import frappe
from frappe import _
from frappe.utils import nowdate, add_days, getdate, flt, cint, date_diff


@frappe.whitelist()
def get_dashboard_data():
    """Return complete dashboard data for CH Item Master.

    Returns:
        dict with keys: kpis, alerts, insights, charts, coverage, pricing_health
    """
    today = nowdate()

    return {
        "kpis": _get_kpis(today),
        "alerts": _get_alerts(today),
        "insights": _get_insights(today),
        "coverage": _get_coverage_data(),
        "pricing_health": _get_pricing_health(today),
        "category_summary": _get_category_summary(),
        "recent_activity": _get_recent_activity(),
        "channel_comparison": _get_channel_comparison(today),
    }


def _get_kpis(today):
    """Core KPI numbers for the top cards."""
    return {
        "total_categories": frappe.db.count("CH Category", {"is_active": 1}),
        "total_sub_categories": frappe.db.count("CH Sub Category", {"is_active": 1}),
        "total_models": frappe.db.count("CH Model", {"is_active": 1}),
        "total_items": frappe.db.count("Item", {
            "ch_model": ("is", "set"),
            "has_variants": 0,
            "disabled": 0,
        }),
        "total_templates": frappe.db.count("Item", {
            "ch_model": ("is", "set"),
            "has_variants": 1,
        }),
        "active_prices": frappe.db.count("CH Item Price", {"status": "Active"}),
        "active_offers": frappe.db.count("CH Item Offer", {"status": "Active"}),
        "active_channels": frappe.db.count("CH Price Channel", {"is_active": 1}),
        "total_manufacturers": frappe.db.count("Manufacturer"),
        "total_brands": frappe.db.count("Brand"),
    }


def _get_alerts(today):
    """Critical alerts that need immediate attention."""
    alerts = []

    # 1. Prices expiring in next 3 days
    expiring_3d = frappe.db.count("CH Item Price", {
        "status": "Active",
        "effective_to": ("between", [today, add_days(today, 3)]),
    })
    if expiring_3d:
        alerts.append({
            "type": "danger",
            "icon": "alert-triangle",
            "title": f"{expiring_3d} price(s) expiring within 3 days",
            "action": "/app/query-report/Expiring Prices?days_ahead=3",
        })

    # 2. Prices expiring in next 7 days
    expiring_7d = frappe.db.count("CH Item Price", {
        "status": "Active",
        "effective_to": ("between", [add_days(today, 4), add_days(today, 7)]),
    })
    if expiring_7d:
        alerts.append({
            "type": "warning",
            "icon": "clock",
            "title": f"{expiring_7d} price(s) expiring in 4-7 days",
            "action": "/app/query-report/Expiring Prices?days_ahead=7",
        })

    # 3. Items without any active price
    items_no_price = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabItem` i
        WHERE i.ch_model IS NOT NULL AND i.ch_model != ''
          AND i.has_variants = 0 AND i.disabled = 0
          AND NOT EXISTS (
            SELECT 1 FROM `tabCH Item Price` p
            WHERE p.item_code = i.item_code AND p.status = 'Active'
          )
    """)[0][0]
    if items_no_price:
        alerts.append({
            "type": "danger" if items_no_price > 10 else "warning",
            "icon": "tag",
            "title": f"{items_no_price} item(s) have no active price",
            "action": "/app/query-report/Items Without Active Price",
        })

    # 4. Pending price approvals
    pending_prices = frappe.db.count("CH Item Price", {"status": "Draft"})
    if pending_prices:
        alerts.append({
            "type": "info",
            "icon": "check-circle",
            "title": f"{pending_prices} price(s) pending approval",
            "action": "/app/ch-item-price?status=Draft",
        })

    # 5. Pending offer approvals
    pending_offers = frappe.db.count("CH Item Offer", {
        "approval_status": "Pending Approval",
    })
    if pending_offers:
        alerts.append({
            "type": "info",
            "icon": "gift",
            "title": f"{pending_offers} offer(s) pending approval",
            "action": "/app/ch-item-offer?approval_status=Pending Approval",
        })

    # 6. Models without items generated
    models_no_items = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabCH Model` m
        WHERE m.is_active = 1
          AND NOT EXISTS (
            SELECT 1 FROM `tabItem` i
            WHERE i.ch_model = m.name
          )
    """)[0][0]
    if models_no_items:
        alerts.append({
            "type": "warning",
            "icon": "box",
            "title": f"{models_no_items} model(s) have no items generated",
            "action": "/app/query-report/Model Coverage",
        })

    return alerts


def _get_insights(today):
    """AI-based insights — pattern detection and recommendations."""
    insights = []

    # 1. Price spread analysis — find items with >20% spread across channels
    spread_items = frappe.db.sql("""
        SELECT p.item_code, i.item_name,
               MIN(p.selling_price) as min_sp,
               MAX(p.selling_price) as max_sp,
               COUNT(DISTINCT p.channel) as channels
        FROM `tabCH Item Price` p
        INNER JOIN `tabItem` i ON i.name = p.item_code
        WHERE p.status = 'Active'
        GROUP BY p.item_code, i.item_name
        HAVING COUNT(DISTINCT p.channel) > 1
          AND MAX(p.selling_price) > 0
          AND ((MAX(p.selling_price) - MIN(p.selling_price)) / MAX(p.selling_price) * 100) > 20
        ORDER BY ((MAX(p.selling_price) - MIN(p.selling_price)) / MAX(p.selling_price) * 100) DESC
        LIMIT 5
    """, as_dict=True)

    if spread_items:
        items_list = ", ".join(f"{r.item_name}" for r in spread_items[:3])
        max_spread = max(
            round((r.max_sp - r.min_sp) / r.max_sp * 100, 1) for r in spread_items
        )
        insights.append({
            "type": "analysis",
            "icon": "bar-chart-2",
            "title": "High Price Spread Detected",
            "description": f"{len(spread_items)} item(s) have >20% price difference "
                           f"across channels (max spread: {max_spread}%). "
                           f"Examples: {items_list}",
            "action": "/app/query-report/Price Comparison Across Channels",
            "severity": "high",
        })

    # 2. Models with partial item coverage
    partial_coverage = frappe.db.sql("""
        SELECT m.name, m.model_name,
               COUNT(DISTINCT i.item_code) as item_count
        FROM `tabCH Model` m
        LEFT JOIN `tabItem` i ON i.ch_model = m.name AND i.has_variants = 0
        WHERE m.is_active = 1
        GROUP BY m.name, m.model_name
        HAVING COUNT(DISTINCT i.item_code) > 0 AND COUNT(DISTINCT i.item_code) < 3
        LIMIT 5
    """, as_dict=True)

    if partial_coverage:
        insights.append({
            "type": "recommendation",
            "icon": "layers",
            "title": "Incomplete Item Generation",
            "description": f"{len(partial_coverage)} model(s) have fewer than 3 variants generated. "
                           f"Consider using 'Generate All Items' to create remaining variants.",
            "action": "/app/query-report/Model Coverage",
            "severity": "medium",
        })

    # 3. Stale pricing — items where price hasn't changed in 90+ days
    stale_prices = frappe.db.sql("""
        SELECT COUNT(DISTINCT p.item_code) as cnt
        FROM `tabCH Item Price` p
        WHERE p.status = 'Active'
          AND p.effective_from <= %(cutoff)s
    """, {"cutoff": add_days(today, -90)})[0][0]

    if stale_prices:
        insights.append({
            "type": "recommendation",
            "icon": "calendar",
            "title": "Stale Pricing Review Needed",
            "description": f"{stale_prices} item(s) have active prices set over 90 days ago. "
                           f"Market conditions may have changed — consider a pricing review.",
            "severity": "low",
        })

    # 4. Offer utilization — active offers vs items covered
    total_active_offers = frappe.db.count("CH Item Offer", {"status": "Active"})
    if total_active_offers:
        items_with_offers = frappe.db.sql("""
            SELECT COUNT(DISTINCT item_code) FROM `tabCH Item Offer`
            WHERE status = 'Active' AND item_code IS NOT NULL AND item_code != ''
        """)[0][0]
        total_priced_items = frappe.db.sql("""
            SELECT COUNT(DISTINCT item_code) FROM `tabCH Item Price`
            WHERE status = 'Active'
        """)[0][0]

        if total_priced_items:
            offer_coverage = round(items_with_offers / total_priced_items * 100, 1)
            insights.append({
                "type": "analysis",
                "icon": "percent",
                "title": f"Offer Coverage: {offer_coverage}%",
                "description": f"{items_with_offers} of {total_priced_items} priced items "
                               f"have active offers. "
                               f"{total_active_offers} total active offers.",
                "severity": "info",
            })

    # 5. Sub-categories without any models
    empty_scs = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabCH Sub Category` sc
        WHERE sc.is_active = 1
          AND NOT EXISTS (
            SELECT 1 FROM `tabCH Model` m WHERE m.sub_category = sc.name
          )
    """)[0][0]
    if empty_scs:
        insights.append({
            "type": "recommendation",
            "icon": "folder-plus",
            "title": f"{empty_scs} Sub-Categories Without Models",
            "description": "These sub-categories have no models created yet. "
                           "They won't produce any items until models are added.",
            "severity": "medium",
        })

    return insights


def _get_coverage_data():
    """Model → Item coverage statistics per category."""
    return frappe.db.sql("""
        SELECT
            COALESCE(sc.category, 'Uncategorized') as category,
            COUNT(DISTINCT m.name) as models,
            COUNT(DISTINCT CASE WHEN i.has_variants = 1 THEN i.item_code END) as templates,
            COUNT(DISTINCT CASE WHEN i.has_variants = 0 THEN i.item_code END) as variants,
            COUNT(DISTINCT CASE
                WHEN i.has_variants = 0 AND ap.item_code IS NOT NULL
                THEN i.item_code END) as priced_variants
        FROM `tabCH Model` m
        LEFT JOIN `tabCH Sub Category` sc ON sc.name = m.sub_category
        LEFT JOIN `tabItem` i ON i.ch_model = m.name AND i.disabled = 0
        LEFT JOIN (
            SELECT DISTINCT item_code FROM `tabCH Item Price` WHERE status = 'Active'
        ) ap ON ap.item_code = i.item_code
        WHERE m.is_active = 1
        GROUP BY sc.category
        ORDER BY models DESC
    """, as_dict=True)


def _get_pricing_health(today):
    """Pricing health metrics."""
    result = frappe.db.sql("""
        SELECT
            status,
            COUNT(*) as cnt
        FROM `tabCH Item Price`
        GROUP BY status
    """, as_dict=True)

    health = {r.status: r.cnt for r in result}

    # Offers breakdown
    offer_result = frappe.db.sql("""
        SELECT status, COUNT(*) as cnt
        FROM `tabCH Item Offer`
        GROUP BY status
    """, as_dict=True)
    offer_health = {r.status: r.cnt for r in offer_result}

    return {
        "prices": health,
        "offers": offer_health,
        "total_prices": sum(health.values()),
        "total_offers": sum(offer_health.values()),
    }


def _get_category_summary():
    """Hierarchical summary: Category → Sub Category → Model counts."""
    return frappe.db.sql("""
        SELECT
            c.name as category,
            c.category_name,
            c.is_active as cat_active,
            COUNT(DISTINCT sc.name) as sub_categories,
            COUNT(DISTINCT m.name) as models,
            COUNT(DISTINCT CASE WHEN i.has_variants = 0 THEN i.item_code END) as items
        FROM `tabCH Category` c
        LEFT JOIN `tabCH Sub Category` sc ON sc.category = c.name AND sc.is_active = 1
        LEFT JOIN `tabCH Model` m ON m.sub_category = sc.name AND m.is_active = 1
        LEFT JOIN `tabItem` i ON i.ch_model = m.name AND i.disabled = 0
        GROUP BY c.name
        ORDER BY items DESC
    """, as_dict=True)


def _get_recent_activity():
    """Recent price/offer/model changes — last 7 days."""
    activity = []

    # Recent prices
    prices = frappe.get_all(
        "CH Item Price",
        filters={"creation": (">=", add_days(nowdate(), -7))},
        fields=["name", "item_code", "item_name", "channel", "status",
                "selling_price", "creation", "modified_by"],
        order_by="creation desc",
        limit=10,
    )
    for p in prices:
        activity.append({
            "type": "price",
            "name": p.name,
            "description": f"Price {p.status}: {p.item_name or p.item_code} on {p.channel}",
            "amount": p.selling_price,
            "timestamp": str(p.creation),
            "user": p.modified_by,
            "link": f"/app/ch-item-price/{p.name}",
        })

    # Recent offers
    offers = frappe.get_all(
        "CH Item Offer",
        filters={"creation": (">=", add_days(nowdate(), -7))},
        fields=["name", "offer_name", "item_code", "status",
                "value", "value_type", "creation", "modified_by"],
        order_by="creation desc",
        limit=5,
    )
    for o in offers:
        activity.append({
            "type": "offer",
            "name": o.name,
            "description": f"Offer: {o.offer_name} ({o.status})",
            "amount": o.value,
            "timestamp": str(o.creation),
            "user": o.modified_by,
            "link": f"/app/ch-item-offer/{o.name}",
        })

    # Recent models
    models = frappe.get_all(
        "CH Model",
        filters={"creation": (">=", add_days(nowdate(), -7))},
        fields=["name", "model_name", "sub_category", "creation", "modified_by"],
        order_by="creation desc",
        limit=5,
    )
    for m in models:
        activity.append({
            "type": "model",
            "name": m.name,
            "description": f"New Model: {m.model_name} ({m.sub_category})",
            "timestamp": str(m.creation),
            "user": m.modified_by,
            "link": f"/app/ch-model/{m.name}",
        })

    # Recent items (CH Item Master managed items)
    items = frappe.get_all(
        "Item",
        filters={
            "creation": (">=", add_days(nowdate(), -7)),
            "ch_model": ("is", "set"),
        },
        fields=["name", "item_name", "ch_model", "ch_sub_category",
                "has_variants", "creation", "modified_by"],
        order_by="creation desc",
        limit=10,
    )
    for it in items:
        item_type = "Template" if it.has_variants else "Variant"
        activity.append({
            "type": "item",
            "name": it.name,
            "description": f"New {item_type}: {it.item_name or it.name}",
            "timestamp": str(it.creation),
            "user": it.modified_by,
            "link": f"/app/item/{it.name}",
        })

    # Sort all by timestamp descending
    activity.sort(key=lambda x: x["timestamp"], reverse=True)
    return activity[:20]


def _get_channel_comparison(today):
    """Per-channel active price statistics."""
    return frappe.db.sql("""
        SELECT
            ch.name as channel,
            ch.channel_name,
            COUNT(DISTINCT p.item_code) as items_priced,
            ROUND(AVG(p.selling_price), 2) as avg_selling_price,
            ROUND(AVG(CASE WHEN p.mrp > 0 THEN (p.mrp - p.selling_price) / p.mrp * 100 END), 1) as avg_discount_pct,
            COUNT(DISTINCT o.item_code) as items_with_offers
        FROM `tabCH Price Channel` ch
        LEFT JOIN `tabCH Item Price` p ON p.channel = ch.name AND p.status = 'Active'
        LEFT JOIN `tabCH Item Offer` o ON o.channel = ch.name AND o.status = 'Active'
        WHERE ch.is_active = 1
        GROUP BY ch.name
        ORDER BY items_priced DESC
    """, as_dict=True)
