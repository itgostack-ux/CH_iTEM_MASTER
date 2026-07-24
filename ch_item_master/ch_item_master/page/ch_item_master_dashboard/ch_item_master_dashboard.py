# Copyright (c) 2026, GoStack and contributors
# Item Master Dashboard — Backend API

"""
Serves all dashboard metrics, alerts, and AI-based insights
in a single API call for the dashboard page.
"""

import frappe
from frappe import _
from frappe.utils import nowdate, add_days, getdate, flt, cint, date_diff

from ch_item_master.config import get_int_setting, is_privileged_user, require_role_setting
from ch_item_master.security import get_company_scope


_DASHBOARD_ROLES = (
    "CH Master Manager",
    "CH Price Manager",
    "CH Offer Manager",
    "CH Warranty Manager",
    "CH Viewer",
    "Stock User",
)
_DASHBOARD_READ_DOCTYPES = (
    "CH Category",
    "CH Sub Category",
    "CH Model",
    "Item",
    "CH Item Price",
    "CH Item Offer",
    "CH Price Channel",
    "Manufacturer",
    "Brand",
)


@frappe.whitelist()
def get_dashboard_data(company=None) -> dict:
    """Return complete dashboard data for CH Item Master.

    Returns:
        dict with keys: kpis, alerts, insights, charts, coverage, pricing_health
    """
    _require_dashboard_access()
    company = _resolve_dashboard_company(company)
    today = nowdate()

    return {
        "company": company,
        "kpis": _get_kpis(today, company),
        "alerts": _get_alerts(today, company),
        "insights": _get_insights(today, company),
        "coverage": _get_coverage_data(company),
        "pricing_health": _get_pricing_health(today, company),
        "category_summary": _get_category_summary(),
        "recent_activity": _get_recent_activity(company),
        "channel_comparison": _get_channel_comparison(today, company),
    }


def _require_dashboard_access():
    require_role_setting(
        "app_access_roles",
        _DASHBOARD_ROLES,
        action="view the Item Master dashboard",
    )
    if is_privileged_user():
        return
    for doctype in _DASHBOARD_READ_DOCTYPES:
        if not frappe.has_permission(doctype, ptype="read", user=frappe.session.user):
            frappe.throw(
                _("You do not have read permission for {0}.").format(doctype),
                frappe.PermissionError,
            )


def _resolve_dashboard_company(company=None):
    company = (company or "").strip()
    if not company and is_privileged_user():
        return None

    if company:
        get_company_scope(requested_company=company)
    else:
        companies = get_company_scope()
        if not companies:
            frappe.throw(
                _("No company is configured in your access scope."),
                frappe.PermissionError,
            )
        if len(companies) != 1:
            frappe.throw(_("Select a company before opening this dashboard."), frappe.ValidationError)
        company = companies[0]

    if not is_privileged_user():
        try:
            from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
        except (ImportError, ModuleNotFoundError):
            frappe.throw(
                _("Location scope validation is unavailable. Contact an administrator."),
                frappe.PermissionError,
            )
        assert_user_has_store_scope(
            company=company,
            user=frappe.session.user,
            msg=_("You are not permitted to view this company's dashboard."),
        )
    return company


def _get_kpis(today, company=None):
    """Core KPI numbers for the top cards."""
    return {
        "total_categories": frappe.db.count("CH Category", {"disabled": 0}),
        "total_sub_categories": frappe.db.count("CH Sub Category", {"disabled": 0}),
        "total_models": frappe.db.count("CH Model", {"disabled": 0}),
        "total_items": frappe.db.count("Item", {
            "ch_model": ("is", "set"),
            "has_variants": 0,
            "disabled": 0,
        }),
        "total_templates": frappe.db.count("Item", {
            "ch_model": ("is", "set"),
            "has_variants": 1,
        }),
        "active_prices": frappe.db.count(
            "CH Item Price",
            {"status": "Active", **({"company": company} if company else {})},
        ),
        "active_offers": frappe.db.count(
            "CH Item Offer",
            {"status": "Active", **({"company": company} if company else {})},
        ),
        "active_channels": frappe.db.count("CH Price Channel", {"disabled": 0}),
        "total_manufacturers": frappe.db.count("Manufacturer"),
        "total_brands": frappe.db.count("Brand"),
    }


def _get_alerts(today, company=None):
    """Critical alerts that need immediate attention."""
    alerts = []
    critical_expiry_days = get_int_setting("item_price_expiry_critical_days", 3)
    warning_expiry_days = get_int_setting("item_price_expiry_warning_days", 7)
    no_price_critical_count = get_int_setting("item_no_price_critical_count", 10)

    # 1. Prices expiring in next 3 days
    expiring_3d = frappe.db.count("CH Item Price", {
        "status": "Active",
        "effective_to": ("between", [today, add_days(today, critical_expiry_days)]),
        **({"company": company} if company else {}),
    })
    if expiring_3d:
        alerts.append({
            "type": "danger",
            "icon": "alert-triangle",
            "title": f"{expiring_3d} price(s) expiring within {critical_expiry_days} days",
            "action": f"/desk/query-report/Expiring Prices?days_ahead={critical_expiry_days}",
        })

    # 2. Prices expiring in next 7 days
    expiring_7d = frappe.db.count("CH Item Price", {
        "status": "Active",
        "effective_to": (
            "between",
            [add_days(today, critical_expiry_days + 1), add_days(today, warning_expiry_days)],
        ),
        **({"company": company} if company else {}),
    })
    if expiring_7d:
        alerts.append({
            "type": "warning",
            "icon": "clock",
            "title": (
                f"{expiring_7d} price(s) expiring in "
                f"{critical_expiry_days + 1}-{warning_expiry_days} days"
            ),
            "action": f"/desk/query-report/Expiring Prices?days_ahead={warning_expiry_days}",
        })

    # 3. Items without any active price
    price_company_clause = " AND p.company = %(company)s" if company else ""
    items_no_price = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabItem` i
        WHERE i.ch_model IS NOT NULL AND i.ch_model != ''
          AND i.has_variants = 0 AND i.disabled = 0
          AND NOT EXISTS (
            SELECT 1 FROM `tabCH Item Price` p
            WHERE p.item_code = i.item_code AND p.status = 'Active'
    """ + price_company_clause + """
          )
    """, {"company": company} if company else {})[0][0]
    if items_no_price:
        alerts.append({
            "type": "danger" if items_no_price > no_price_critical_count else "warning",
            "icon": "tag",
            "title": f"{items_no_price} item(s) have no active price",
            "action": "/desk/query-report/Items Without Active Price",
        })

    # 4. Pending price approvals
    pending_prices = frappe.db.count(
        "CH Item Price",
        {"status": "Draft", **({"company": company} if company else {})},
    )
    if pending_prices:
        alerts.append({
            "type": "info",
            "icon": "check-circle",
            "title": f"{pending_prices} price(s) pending approval",
            "action": "/desk/ch-item-price?status=Draft",
        })

    # 5. Pending offer approvals
    pending_offers = frappe.db.count("CH Item Offer", {
        "approval_status": "Pending Approval",
        **({"company": company} if company else {}),
    })
    if pending_offers:
        alerts.append({
            "type": "info",
            "icon": "gift",
            "title": f"{pending_offers} offer(s) pending approval",
            "action": "/desk/ch-item-offer?approval_status=Pending Approval",
        })

    # 6. Models without items generated
    models_no_items = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabCH Model` m
        WHERE m.disabled = 0
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
            "action": "/desk/query-report/Model Coverage",
        })

    return alerts


def _get_insights(today, company=None):
    """AI-based insights — pattern detection and recommendations."""
    insights = []
    price_company_clause = " AND p.company = %(company)s" if company else ""
    plain_price_company_clause = " AND company = %(company)s" if company else ""
    offer_company_clause = " AND company = %(company)s" if company else ""
    company_params = {"company": company} if company else {}
    price_spread_percent = get_int_setting("item_price_spread_percent", 20)
    partial_model_variant_count = get_int_setting("item_partial_model_variant_count", 3, minimum=1)
    stale_price_days = get_int_setting("item_stale_price_days", 90)
    insight_params = {
        **company_params,
        "price_spread_percent": price_spread_percent,
        "partial_model_variant_count": partial_model_variant_count,
    }

    # 1. Price spread analysis — find items with >20% spread across channels
    spread_items = frappe.db.sql("""
        SELECT p.item_code, i.item_name,
               MIN(p.selling_price) as min_sp,
               MAX(p.selling_price) as max_sp,
               COUNT(DISTINCT p.channel) as channels
        FROM `tabCH Item Price` p
        INNER JOIN `tabItem` i ON i.name = p.item_code
        WHERE p.status = 'Active'
    """ + price_company_clause + """
        GROUP BY p.item_code, i.item_name
        HAVING COUNT(DISTINCT p.channel) > 1
          AND MAX(p.selling_price) > 0
          AND ((MAX(p.selling_price) - MIN(p.selling_price)) / MAX(p.selling_price) * 100)
              > %(price_spread_percent)s
        ORDER BY ((MAX(p.selling_price) - MIN(p.selling_price)) / MAX(p.selling_price) * 100) DESC
        LIMIT 5
    """, insight_params, as_dict=True)

    if spread_items:
        items_list = ", ".join(f"{r.item_name}" for r in spread_items[:3])
        max_spread = max(
            round((r.max_sp - r.min_sp) / r.max_sp * 100, 1) for r in spread_items
        )
        insights.append({
            "type": "analysis",
            "icon": "bar-chart-2",
            "title": "High Price Spread Detected",
            "description": f"{len(spread_items)} item(s) have >{price_spread_percent}% price difference "
                           f"across channels (max spread: {max_spread}%). "
                           f"Examples: {items_list}",
            "action": "/desk/query-report/Price Comparison Across Channels",
            "severity": "high",
        })

    # 2. Models with partial item coverage
    partial_coverage = frappe.db.sql("""
        SELECT m.name, m.model_name,
               COUNT(DISTINCT i.item_code) as item_count
        FROM `tabCH Model` m
        LEFT JOIN `tabItem` i ON i.ch_model = m.name AND i.has_variants = 0
        WHERE m.disabled = 0
        GROUP BY m.name, m.model_name
        HAVING COUNT(DISTINCT i.item_code) > 0
           AND COUNT(DISTINCT i.item_code) < %(partial_model_variant_count)s
        LIMIT 5
    """, insight_params, as_dict=True)

    if partial_coverage:
        insights.append({
            "type": "recommendation",
            "icon": "layers",
            "title": "Incomplete Item Generation",
            "description": (
                f"{len(partial_coverage)} model(s) have fewer than "
                f"{partial_model_variant_count} variants generated. "
                "Consider using 'Generate All Items' to create remaining variants."
            ),
            "action": "/desk/query-report/Model Coverage",
            "severity": "medium",
        })

    # 3. Stale pricing — items where price hasn't changed in 90+ days
    stale_prices = frappe.db.sql("""
        SELECT COUNT(DISTINCT p.item_code) as cnt
        FROM `tabCH Item Price` p
        WHERE p.status = 'Active'
          AND p.effective_from <= %(cutoff)s
    """ + price_company_clause, {
        "cutoff": add_days(today, -stale_price_days),
        **company_params,
    })[0][0]

    if stale_prices:
        insights.append({
            "type": "recommendation",
            "icon": "calendar",
            "title": "Stale Pricing Review Needed",
            "description": f"{stale_prices} item(s) have active prices set over {stale_price_days} days ago. "
                           f"Market conditions may have changed — consider a pricing review.",
            "severity": "low",
        })

    # 4. Offer utilization — active offers vs items covered
    total_active_offers = frappe.db.count(
        "CH Item Offer",
        {"status": "Active", **({"company": company} if company else {})},
    )
    if total_active_offers:
        items_with_offers = frappe.db.sql("""
            SELECT COUNT(DISTINCT item_code) FROM `tabCH Item Offer`
            WHERE status = 'Active' AND item_code IS NOT NULL AND item_code != ''
        """ + offer_company_clause, company_params)[0][0]
        total_priced_items = frappe.db.sql("""
            SELECT COUNT(DISTINCT item_code) FROM `tabCH Item Price`
            WHERE status = 'Active'
        """ + plain_price_company_clause, company_params)[0][0]

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
        WHERE sc.disabled = 0
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


def _get_coverage_data(company=None):
    """Model → Item coverage statistics per category."""
    company_clause = " AND company = %(company)s" if company else ""
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
    """ + company_clause + """
        ) ap ON ap.item_code = i.item_code
        WHERE m.disabled = 0
        GROUP BY sc.category
        ORDER BY models DESC
    """, {"company": company} if company else {}, as_dict=True)


def _get_pricing_health(today, company=None):
    """Pricing health metrics."""
    company_clause = " WHERE company = %(company)s" if company else ""
    company_params = {"company": company} if company else {}
    result = frappe.db.sql("""
        SELECT
            status,
            COUNT(*) as cnt
        FROM `tabCH Item Price`
    """ + company_clause + """
        GROUP BY status
    """, company_params, as_dict=True)

    health = {r.status: r.cnt for r in result}

    # Offers breakdown
    offer_result = frappe.db.sql("""
        SELECT status, COUNT(*) as cnt
        FROM `tabCH Item Offer`
    """ + company_clause + """
        GROUP BY status
    """, company_params, as_dict=True)
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
            c.disabled as cat_disabled,
            COUNT(DISTINCT sc.name) as sub_categories,
            COUNT(DISTINCT m.name) as models,
            COUNT(DISTINCT CASE WHEN i.has_variants = 0 THEN i.item_code END) as items
        FROM `tabCH Category` c
        LEFT JOIN `tabCH Sub Category` sc ON sc.category = c.name AND sc.disabled = 0
        LEFT JOIN `tabCH Model` m ON m.sub_category = sc.name AND m.disabled = 0
        LEFT JOIN `tabItem` i ON i.ch_model = m.name AND i.disabled = 0
        GROUP BY c.name
        ORDER BY items DESC
    """, as_dict=True)


def _get_recent_activity(company=None):
    """Recent price/offer/model changes — last 7 days."""
    activity = []

    # Recent prices
    prices = frappe.get_all(
        "CH Item Price",
        filters={
            "creation": (">=", add_days(nowdate(), -7)),
            **({"company": company} if company else {}),
        },
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
            "link": f"/desk/ch-item-price/{p.name}",
        })

    # Recent offers
    offers = frappe.get_all(
        "CH Item Offer",
        filters={
            "creation": (">=", add_days(nowdate(), -7)),
            **({"company": company} if company else {}),
        },
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
            "link": f"/desk/ch-item-offer/{o.name}",
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
            "link": f"/desk/ch-model/{m.name}",
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
            "link": f"/desk/item/{it.name}",
        })

    # Sort all by timestamp descending
    activity.sort(key=lambda x: x["timestamp"], reverse=True)
    return activity[:20]


def _get_channel_comparison(today, company=None):
    """Per-channel active price statistics."""
    price_company_clause = " AND p.company = %(company)s" if company else ""
    offer_company_clause = " AND o.company = %(company)s" if company else ""
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
    """ + price_company_clause + """
        LEFT JOIN `tabCH Item Offer` o ON o.channel = ch.name AND o.status = 'Active'
    """ + offer_company_clause + """
        WHERE ch.disabled = 0
        GROUP BY ch.name
        ORDER BY items_priced DESC
    """, {"company": company} if company else {}, as_dict=True)
