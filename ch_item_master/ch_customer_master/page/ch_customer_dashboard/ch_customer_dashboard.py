# Copyright (c) 2026, GoStack and contributors
# CH Customer Dashboard — Backend API

"""
AI-powered Customer Dashboard for Corporate, Marketing and Store teams.

Company scoping rules (derived from BRD analysis):
─────────────────────────────────────────────────
COMMON (cross-company, never filtered):
  • Devices, VAS, Device Analytics — GoGizmo VAS visible in GoFix and vice versa
  • Loyalty — always overall
  • Referral stats — customer-level, not company-level
  • Alerts about customers (dormant, churned, KYC pending, duplicates)
  • Alerts about loyalty expiry
  • Alerts about warranty expiry (device-level, cross-company)

COMPANY-FILTERED (when a company is selected):
  • Total Customers = customers who transacted with this company
  • New This Month = first-time transactors at this company this month
  • VIP / Segments = segments among this company's customer set
  • Active Customers = active among this company's customers
  • Revenue, Avg Spend = this company's invoices only
  • Top Customers = ranked by spend at this company
  • Store Performance = this company's store visits
  • Revenue Trend = this company only
  • Recent Activity = customer registrations always; transactions filtered
  • Insights (revenue concentration) = company-specific

ACCESS CONTROL:
  • User Permission on Company restricts the dropdown
  • System Manager / Administrator see all
"""

import frappe
from frappe import _
from frappe.utils import (
    nowdate, add_days, add_months, getdate, flt, cint,
    date_diff, now_datetime, get_first_day, get_last_day,
)


# ── Permission Helpers ───────────────────────────────────────────────────────

def _get_allowed_companies():
    """Return companies the current user may view."""
    user = frappe.session.user
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return frappe.get_all("Company", pluck="name", order_by="name")

    permitted = frappe.get_all(
        "User Permission",
        filters={"user": user, "allow": "Company"},
        pluck="for_value",
    )
    if permitted:
        return permitted

    default = frappe.defaults.get_user_default("Company", user)
    return [default] if default else frappe.get_all("Company", pluck="name", order_by="name")


def _validate_company(company):
    """Validate and normalise the company argument."""
    if not company or company == "All":
        return None
    allowed = _get_allowed_companies()
    if company not in allowed:
        frappe.throw(_("You do not have permission to view data for {0}").format(company))
    return company


def _co_cond(company, alias="si", field="company"):
    """SQL fragment:  AND alias.field = %(company)s   (empty if no filter)."""
    return f" AND {alias}.{field} = %(company)s" if company else ""


def _co_params(company):
    return {"company": company} if company else {}


def _customers_of_company_subquery(company):
    """SQL subquery returning customer names that transacted with *company*.

    Covers Sales Invoice, Service Request and CH Customer Store Visit.
    Returns None string '' if no company filter.
    """
    if not company:
        return None
    return """(
        SELECT DISTINCT _x.customer FROM (
            SELECT customer FROM `tabSales Invoice`
            WHERE docstatus = 1 AND company = %(company)s
            UNION
            SELECT customer FROM `tabService Request`
            WHERE company = %(company)s AND docstatus < 2
            UNION
            SELECT parent AS customer FROM `tabCH Customer Store Visit`
            WHERE company = %(company)s
        ) _x
    )"""


# ── Public APIs ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_allowed_companies():
    """Return companies the current user can access (for the dropdown)."""
    companies = _get_allowed_companies()
    return {"companies": companies, "show_all": len(companies) > 1}


@frappe.whitelist()
def get_dashboard_data(company=None):
    """Return complete dashboard payload.

    Args:
        company: company name or None/"All" for all.
    """
    company = _validate_company(company)
    today = nowdate()
    month_start = str(get_first_day(today))

    return {
        "selected_company": company or "All",
        "kpis":              _get_kpis(today, month_start, company),
        "alerts":            _get_alerts(today, company),
        "insights":          _get_insights(today, company),
        "segments":          _get_segment_distribution(company),
        "loyalty_overview":  _get_loyalty_overview(),               # ALWAYS overall
        "company_breakdown": _get_company_breakdown(today, company),
        "top_customers":     _get_top_customers(company),
        "device_analytics":  _get_device_analytics(),               # ALWAYS common
        "recent_activity":   _get_recent_activity(company),
        "revenue_trend":     _get_revenue_trend(today, company),
        "referral_stats":    _get_referral_stats(),                 # ALWAYS common
        "store_performance": _get_store_performance(today, company),
    }


# ── KPIs ─────────────────────────────────────────────────────────────────────

def _get_kpis(today, month_start, company):
    """Core KPI cards."""
    p = _co_params(company)
    cond = _co_cond(company, "si")
    cust_sub = _customers_of_company_subquery(company)

    # ─ Total Customers (company-scoped when filtered) ────────────────────
    if company:
        total_customers = frappe.db.sql(f"""
            SELECT COUNT(DISTINCT _x.customer) FROM (
                SELECT customer FROM `tabSales Invoice`
                WHERE docstatus = 1 AND company = %(company)s
                UNION
                SELECT customer FROM `tabService Request`
                WHERE company = %(company)s AND docstatus < 2
                UNION
                SELECT parent AS customer FROM `tabCH Customer Store Visit`
                WHERE company = %(company)s
            ) _x
        """, p)[0][0] or 0
    else:
        total_customers = frappe.db.count("Customer", {"disabled": 0})

    # ─ New This Month ────────────────────────────────────────────────────
    # Company-filtered: first transaction at THIS company this month
    # (existing GoGizmo customer doing first GoFix transaction = new to GoFix)
    if company:
        new_this_month = frappe.db.sql("""
            SELECT COUNT(DISTINCT si.customer)
            FROM `tabSales Invoice` si
            WHERE si.docstatus = 1 AND si.company = %(company)s
              AND si.posting_date >= %(start)s
              AND NOT EXISTS (
                SELECT 1 FROM `tabSales Invoice` s2
                WHERE s2.customer = si.customer AND s2.docstatus = 1
                  AND s2.company = %(company)s AND s2.posting_date < %(start)s
              )
        """, {**p, "start": month_start})[0][0] or 0
    else:
        new_this_month = frappe.db.count("Customer", {
            "creation": (">=", month_start), "disabled": 0,
        })

    # ─ VIP Count (company-scoped customer set) ───────────────────────────
    if company:
        vip_count = frappe.db.sql(f"""
            SELECT COUNT(*) FROM `tabCustomer` c
            WHERE c.disabled = 0 AND c.ch_customer_segment = 'VIP'
              AND c.name IN {cust_sub}
        """, p)[0][0] or 0
    else:
        vip_count = frappe.db.count("Customer", {"ch_customer_segment": "VIP", "disabled": 0})

    # ─ Active Customers (visited any store in last 90d, company-scoped) ──
    if company:
        active_customers = frappe.db.sql(f"""
            SELECT COUNT(*) FROM `tabCustomer` c
            WHERE c.disabled = 0 AND c.ch_last_visit_date >= %(cutoff)s
              AND c.name IN {cust_sub}
        """, {**p, "cutoff": add_days(today, -90)})[0][0] or 0
    else:
        active_customers = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabCustomer`
            WHERE disabled = 0 AND ch_last_visit_date >= %(cutoff)s
        """, {"cutoff": add_days(today, -90)})[0][0] or 0

    # ─ Revenue (always company-filtered) ─────────────────────────────────
    total_revenue = frappe.db.sql(f"""
        SELECT IFNULL(SUM(si.grand_total), 0)
        FROM `tabSales Invoice` si
        WHERE si.docstatus = 1 {cond}
    """, p)[0][0]

    revenue_this_month = frappe.db.sql(f"""
        SELECT IFNULL(SUM(si.grand_total), 0)
        FROM `tabSales Invoice` si
        WHERE si.docstatus = 1 AND si.posting_date >= %(start)s {cond}
    """, {**p, "start": month_start})[0][0]

    avg_spend = flt(total_revenue / max(total_customers, 1), 0)

    # ─ Loyalty — ALWAYS overall ──────────────────────────────────────────
    total_loyalty = 0
    if frappe.db.exists("DocType", "CH Loyalty Transaction"):
        total_loyalty = frappe.db.sql("""
            SELECT IFNULL(SUM(points), 0) FROM `tabCH Loyalty Transaction`
            WHERE docstatus = 1 AND is_expired = 0
        """)[0][0]

    # ─ Devices — ALWAYS common (cross-company) ──────────────────────────
    total_devices = 0
    if frappe.db.exists("DocType", "CH Customer Device"):
        total_devices = frappe.db.count("CH Customer Device")

    # ─ KYC (company-scoped customer set) ─────────────────────────────────
    if company:
        kyc_verified = frappe.db.sql(f"""
            SELECT COUNT(*) FROM `tabCustomer` c
            WHERE c.disabled = 0 AND c.ch_kyc_verified = 1
              AND c.name IN {cust_sub}
        """, p)[0][0] or 0
    else:
        kyc_verified = frappe.db.count("Customer", {"ch_kyc_verified": 1, "disabled": 0})

    return {
        "total_customers": cint(total_customers),
        "new_this_month": cint(new_this_month),
        "vip_count": cint(vip_count),
        "active_customers": cint(active_customers),
        "total_revenue": flt(total_revenue),
        "revenue_this_month": flt(revenue_this_month),
        "avg_spend": flt(avg_spend),
        "total_loyalty": cint(total_loyalty),
        "total_devices": cint(total_devices),
        "kyc_verified": cint(kyc_verified),
        "kyc_total": cint(total_customers),
    }


# ── Alerts ───────────────────────────────────────────────────────────────────

def _get_alerts(today, company):
    """Critical alerts. Customer-level alerts are COMMON; loyalty ALWAYS overall."""
    alerts = []

    # 1 — Dormant customers (COMMON — segment is overall)
    dormant = frappe.db.count("Customer", {"ch_customer_segment": "Dormant", "disabled": 0})
    if dormant:
        alerts.append({
            "type": "warning", "icon": "alert-triangle",
            "title": f"{dormant} customer(s) marked Dormant (no visit in 6+ months)",
            "action": "/app/customer?ch_customer_segment=Dormant",
        })

    # 2 — Churned customers (COMMON)
    churned = frappe.db.count("Customer", {"ch_customer_segment": "Churned", "disabled": 0})
    if churned:
        alerts.append({
            "type": "danger", "icon": "user-minus",
            "title": f"{churned} customer(s) classified as Churned — win-back needed",
            "action": "/app/customer?ch_customer_segment=Churned",
        })

    # 3 — Loyalty expiring (ALWAYS overall)
    if frappe.db.exists("DocType", "CH Loyalty Transaction"):
        exp = frappe.db.sql("""
            SELECT COUNT(DISTINCT customer) as customers,
                   IFNULL(SUM(points), 0) as points
            FROM `tabCH Loyalty Transaction`
            WHERE docstatus = 1 AND is_expired = 0
              AND expiry_date BETWEEN %(today)s AND %(cutoff)s AND points > 0
        """, {"today": today, "cutoff": add_days(today, 30)}, as_dict=True)
        if exp and exp[0].customers:
            e = exp[0]
            alerts.append({
                "type": "warning", "icon": "clock",
                "title": f"{cint(e.points)} loyalty pts expiring for {cint(e.customers)} customer(s) in 30 days",
                "action": "/app/ch-loyalty-transaction?is_expired=0",
            })

    # 4 — KYC pending (COMMON)
    kyc_pending = frappe.db.count("Customer", {
        "ch_kyc_verified": 0, "disabled": 0, "ch_total_purchases": (">", 0),
    })
    if kyc_pending:
        alerts.append({
            "type": "info", "icon": "file-text",
            "title": f"{kyc_pending} active customer(s) without KYC verification",
            "action": "/app/customer?ch_kyc_verified=0",
        })

    # 5 — Duplicate phones (COMMON)
    dupes = frappe.db.sql("""
        SELECT mobile_no, COUNT(*) as cnt FROM `tabCustomer`
        WHERE mobile_no IS NOT NULL AND mobile_no != '' AND disabled = 0
        GROUP BY mobile_no HAVING COUNT(*) > 1 LIMIT 5
    """, as_dict=True)
    if dupes:
        alerts.append({
            "type": "warning", "icon": "users",
            "title": f"{len(dupes)} phone number(s) shared by multiple customers — potential duplicates",
            "action": "/app/customer?mobile_no=%5B%22is%22%2C%22set%22%5D&order_by=mobile_no",
        })

    # 6 — Warranty expiring (COMMON — device-level, cross-company)
    if frappe.db.exists("DocType", "CH Customer Device"):
        we = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabCH Customer Device`
            WHERE warranty_expiry BETWEEN %(today)s AND %(cutoff)s
              AND current_status = 'Owned'
        """, {"today": today, "cutoff": add_days(today, 30)})[0][0]
        if we:
            alerts.append({
                "type": "info", "icon": "shield",
                "title": f"{we} device warranty(ies) expiring in 30 days — upsell opportunity",
                "action": "/app/ch-customer-device?current_status=Owned",
            })

    return alerts


# ── AI Insights ──────────────────────────────────────────────────────────────

def _get_insights(today, company):
    """AI insights. Segment/referral/VAS = COMMON. Revenue = company-specific."""
    insights = []

    # 1 — Segment analysis (COMMON — segment is overall attribute)
    segments = frappe.db.sql("""
        SELECT ch_customer_segment as segment, COUNT(*) as cnt
        FROM `tabCustomer`
        WHERE disabled = 0 AND ch_customer_segment IS NOT NULL
          AND ch_customer_segment != ''
        GROUP BY ch_customer_segment
    """, as_dict=True)
    segment_map = {s.segment: s.cnt for s in segments}
    total_segged = sum(segment_map.values())

    if total_segged > 0:
        vip_pct = round(segment_map.get("VIP", 0) / total_segged * 100, 1)
        dormant_pct = round(segment_map.get("Dormant", 0) / total_segged * 100, 1)
        churned_pct = round(segment_map.get("Churned", 0) / total_segged * 100, 1)

        if vip_pct > 0:
            insights.append({
                "type": "analysis", "icon": "star",
                "title": f"VIP Concentration: {vip_pct}%",
                "description": f"{segment_map.get('VIP', 0)} VIP customer(s). Focus retention on this group.",
                "severity": "info",
                "action": "/app/customer?ch_customer_segment=VIP",
            })
        if dormant_pct > 25:
            insights.append({
                "type": "recommendation", "icon": "alert-circle",
                "title": f"High Dormancy: {dormant_pct}% customers inactive",
                "description": f"{segment_map.get('Dormant', 0)} customers inactive 6+ months. "
                               "Consider WhatsApp/SMS re-engagement campaign.",
                "severity": "high",
                "action": "/app/customer?ch_customer_segment=Dormant",
            })
        if churned_pct > 10:
            insights.append({
                "type": "recommendation", "icon": "x-circle",
                "title": f"Churn Alert: {churned_pct}% customers churned",
                "description": f"{segment_map.get('Churned', 0)} customers likely left. Run targeted win-back offers.",
                "severity": "high",
                "action": "/app/customer?ch_customer_segment=Churned",
            })

    # 2 — Revenue concentration (COMPANY-SPECIFIC)
    cond = _co_cond(company, "si")
    p = _co_params(company)
    inv_count = frappe.db.sql(
        f"SELECT COUNT(*) FROM `tabSales Invoice` si WHERE si.docstatus = 1 {cond}", p
    )[0][0]

    if inv_count:
        top_revenue = frappe.db.sql(f"""
            SELECT si.customer, SUM(si.grand_total) as total
            FROM `tabSales Invoice` si
            WHERE si.docstatus = 1 {cond}
            GROUP BY si.customer ORDER BY total DESC LIMIT 5
        """, p, as_dict=True)

        total_rev = frappe.db.sql(
            f"SELECT IFNULL(SUM(si.grand_total),0) FROM `tabSales Invoice` si WHERE si.docstatus=1 {cond}", p
        )[0][0]

        if top_revenue and total_rev:
            top5_rev = sum(r.total for r in top_revenue)
            top5_pct = round(top5_rev / total_rev * 100, 1)
            label = f" ({company})" if company else ""
            if top5_pct > 50:
                insights.append({
                    "type": "analysis", "icon": "alert-triangle",
                    "title": f"Revenue Concentration Risk{label}: Top 5 = {top5_pct}%",
                    "description": "Top 5 customers contribute over half of total revenue. "
                                   "Diversify customer base to reduce dependency risk.",
                    "severity": "high",
                })

    # 3 — Referral (COMMON)
    total_with_ref = frappe.db.count("Customer", {"ch_referral_code": ("is", "set"), "disabled": 0})
    total_referred = frappe.db.count("Customer", {"ch_referred_by": ("is", "set"), "disabled": 0})
    if total_with_ref and total_referred:
        ref_rate = round(total_referred / total_with_ref * 100, 1)
        insights.append({
            "type": "analysis", "icon": "share-2",
            "title": f"Referral Conversion: {ref_rate}%",
            "description": f"{total_referred} joined via referral out of {total_with_ref} with codes. "
                           f"{'Boost incentives.' if ref_rate < 5 else 'Program performing well.'}",
            "severity": "medium" if ref_rate < 5 else "low",
        })

    # 4 — Devices without VAS (COMMON — cross-company)
    if frappe.db.exists("DocType", "CH Customer Device"):
        no_vas = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabCH Customer Device` d
            WHERE d.current_status = 'Owned' AND NOT EXISTS (
                SELECT 1 FROM `tabCH Customer Device VAS` v
                WHERE v.parent = d.name AND v.status IN ('Active', 'Pending')
            )
        """)[0][0]
        if no_vas:
            insights.append({
                "type": "recommendation", "icon": "shield",
                "title": f"{no_vas} Device(s) Without VAS/Warranty",
                "description": "Owned devices with no protection plan. Push VAS upsell.",
                "severity": "medium",
                "action": "/app/ch-customer-device?current_status=Owned",
            })

    # 5 — New→Regular conversion (COMMON)
    new_c = frappe.db.count("Customer", {"ch_customer_segment": "New", "disabled": 0})
    reg_c = frappe.db.count("Customer", {"ch_customer_segment": "Regular", "disabled": 0})
    if new_c and (new_c + reg_c) > 0:
        conv = round(reg_c / (new_c + reg_c) * 100, 1)
        insights.append({
            "type": "analysis", "icon": "trending-up",
            "title": f"New→Regular Conversion: {conv}%",
            "description": f"{new_c} new customer(s) yet to repeat. Consider first-purchase incentives.",
            "severity": "medium" if conv < 30 else "low",
        })

    # 6 — High-value unsubscribed (COMMON)
    unsub_vip = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabCustomer`
        WHERE ch_is_subscribed = 0 AND ch_total_purchases > 50000 AND disabled = 0
    """)[0][0]
    if unsub_vip:
        insights.append({
            "type": "recommendation", "icon": "mail",
            "title": f"{unsub_vip} High-Value Customer(s) Unsubscribed",
            "description": "Rs.50k+ spenders opted out of communications. Personalized outreach recommended.",
            "severity": "medium",
        })

    return insights


# ── Segment Distribution ────────────────────────────────────────────────────

def _get_segment_distribution(company):
    """Segment breakdown. Company-filtered = among that company's customers."""
    if company:
        cust_sub = _customers_of_company_subquery(company)
        return frappe.db.sql(f"""
            SELECT
                IFNULL(NULLIF(c.ch_customer_segment, ''), 'Unclassified') as segment,
                COUNT(*) as count
            FROM `tabCustomer` c
            WHERE c.disabled = 0 AND c.name IN {cust_sub}
            GROUP BY segment ORDER BY count DESC
        """, _co_params(company), as_dict=True)
    else:
        return frappe.db.sql("""
            SELECT
                IFNULL(NULLIF(ch_customer_segment, ''), 'Unclassified') as segment,
                COUNT(*) as count
            FROM `tabCustomer` WHERE disabled = 0
            GROUP BY segment ORDER BY count DESC
        """, as_dict=True)


# ── Loyalty Overview — ALWAYS GLOBAL ─────────────────────────────────────────

def _get_loyalty_overview():
    """Loyalty points summary across ALL companies. Never filtered."""
    if not frappe.db.exists("DocType", "CH Loyalty Transaction"):
        return {"total_balance": 0, "earned": 0, "redeemed": 0, "expired": 0, "by_type": []}

    summary = frappe.db.sql("""
        SELECT transaction_type,
               IFNULL(SUM(ABS(points)), 0) as total_points,
               COUNT(*) as txn_count
        FROM `tabCH Loyalty Transaction` WHERE docstatus = 1
        GROUP BY transaction_type
    """, as_dict=True)
    by_type = {r.transaction_type: {"points": flt(r.total_points), "count": r.txn_count} for r in summary}

    total_balance = frappe.db.sql("""
        SELECT IFNULL(SUM(points), 0) FROM `tabCH Loyalty Transaction`
        WHERE docstatus = 1 AND is_expired = 0
    """)[0][0]

    customers_enrolled = frappe.db.sql("""
        SELECT COUNT(DISTINCT customer) FROM `tabCH Loyalty Transaction`
        WHERE docstatus = 1
    """)[0][0]

    return {
        "total_balance": cint(total_balance),
        "earned": flt(by_type.get("Earn", {}).get("points", 0)),
        "redeemed": flt(by_type.get("Redeem", {}).get("points", 0)),
        "expired": flt(by_type.get("Expire", {}).get("points", 0)),
        "referral_bonus": flt(by_type.get("Referral Bonus", {}).get("points", 0)),
        "customers_enrolled": cint(customers_enrolled),
        "by_type": summary,
    }


# ── Company Breakdown ───────────────────────────────────────────────────────

def _get_company_breakdown(today, company):
    """Per-company customer activity. When filtered, shows only that company."""
    cond = _co_cond(company, "si")
    p = _co_params(company)
    return frappe.db.sql(f"""
        SELECT
            si.company,
            COUNT(DISTINCT si.customer) as customers,
            COUNT(*) as transactions,
            IFNULL(SUM(si.grand_total), 0) as revenue,
            IFNULL(ROUND(AVG(si.grand_total), 0), 0) as avg_ticket,
            MAX(si.posting_date) as last_transaction
        FROM `tabSales Invoice` si
        WHERE si.docstatus = 1 {cond}
        GROUP BY si.company ORDER BY revenue DESC
    """, p, as_dict=True)


# ── Top Customers ────────────────────────────────────────────────────────────

def _get_top_customers(company):
    """Top 10 customers. Company-filtered = ranked by spend at that company."""
    if company:
        return frappe.db.sql("""
            SELECT
                c.name as customer, c.customer_name, c.mobile_no,
                IFNULL(c.ch_customer_segment, 'New') as segment,
                IFNULL(SUM(si.grand_total), 0) as total_spend,
                IFNULL(c.ch_total_services, 0) as total_services,
                IFNULL(c.ch_total_buybacks, 0) as total_buybacks,
                IFNULL(c.ch_active_devices, 0) as devices,
                IFNULL(c.ch_loyalty_points_balance, 0) as loyalty_balance,
                c.ch_last_visit_date as last_visit,
                c.ch_customer_since as customer_since
            FROM `tabCustomer` c
            INNER JOIN `tabSales Invoice` si
              ON si.customer = c.name AND si.docstatus = 1 AND si.company = %(company)s
            WHERE c.disabled = 0
            GROUP BY c.name
            ORDER BY total_spend DESC
            LIMIT 10
        """, {"company": company}, as_dict=True)
    else:
        return frappe.db.sql("""
            SELECT
                c.name as customer, c.customer_name, c.mobile_no,
                IFNULL(c.ch_customer_segment, 'New') as segment,
                IFNULL(c.ch_total_purchases, 0) as total_spend,
                IFNULL(c.ch_total_services, 0) as total_services,
                IFNULL(c.ch_total_buybacks, 0) as total_buybacks,
                IFNULL(c.ch_active_devices, 0) as devices,
                IFNULL(c.ch_loyalty_points_balance, 0) as loyalty_balance,
                c.ch_last_visit_date as last_visit,
                c.ch_customer_since as customer_since
            FROM `tabCustomer` c
            WHERE c.disabled = 0
            ORDER BY IFNULL(c.ch_total_purchases, 0) DESC
            LIMIT 10
        """, as_dict=True)


# ── Device Analytics — ALWAYS COMMON (cross-company) ────────────────────────

def _get_device_analytics():
    """Device + VAS analytics. ALWAYS global — GoGizmo VAS visible in GoFix."""
    if not frappe.db.exists("DocType", "CH Customer Device"):
        return {"by_status": [], "by_brand": [], "total": 0, "with_vas": 0, "vas_adoption_pct": 0}

    by_status = frappe.db.sql("""
        SELECT current_status as status, COUNT(*) as count
        FROM `tabCH Customer Device` GROUP BY current_status ORDER BY count DESC
    """, as_dict=True)

    by_brand = frappe.db.sql("""
        SELECT IFNULL(NULLIF(brand, ''), 'Unknown') as brand, COUNT(*) as count
        FROM `tabCH Customer Device` GROUP BY brand ORDER BY count DESC LIMIT 10
    """, as_dict=True)

    total = frappe.db.count("CH Customer Device")

    with_vas = frappe.db.sql("""
        SELECT COUNT(DISTINCT d.name) FROM `tabCH Customer Device` d
        WHERE EXISTS (
            SELECT 1 FROM `tabCH Customer Device VAS` v
            WHERE v.parent = d.name AND v.status = 'Active'
        )
    """)[0][0]

    return {
        "by_status": by_status,
        "by_brand": by_brand,
        "total": cint(total),
        "with_vas": cint(with_vas),
        "vas_adoption_pct": round(with_vas / max(total, 1) * 100, 1),
    }


# ── Recent Activity ──────────────────────────────────────────────────────────

def _get_recent_activity(company):
    """Last 7 days. Customer registrations = COMMON. Transactions = filtered."""
    activity = []
    seven_days_ago = add_days(nowdate(), -7)

    # New customers — ALWAYS common
    for c in frappe.get_all(
        "Customer",
        filters={"creation": (">=", seven_days_ago), "disabled": 0},
        fields=["name", "customer_name", "mobile_no", "creation", "owner"],
        order_by="creation desc", limit=10,
    ):
        activity.append({
            "type": "customer",
            "description": f"New Customer: {c.customer_name}",
            "detail": c.mobile_no or "",
            "timestamp": str(c.creation),
            "user": c.owner,
            "link": f"/app/customer/{c.name}",
        })

    # Invoices — company-filtered
    inv_filters = {"creation": (">=", seven_days_ago), "docstatus": 1}
    if company:
        inv_filters["company"] = company
    for inv in frappe.get_all(
        "Sales Invoice", filters=inv_filters,
        fields=["name", "customer", "customer_name", "grand_total", "company", "creation", "owner"],
        order_by="creation desc", limit=10,
    ):
        activity.append({
            "type": "purchase",
            "description": f"Purchase: {inv.customer_name}",
            "detail": inv.company,
            "amount": inv.grand_total,
            "timestamp": str(inv.creation),
            "user": inv.owner,
            "link": f"/app/sales-invoice/{inv.name}",
        })

    # Loyalty — ALWAYS overall
    if frappe.db.exists("DocType", "CH Loyalty Transaction"):
        for lt in frappe.get_all(
            "CH Loyalty Transaction",
            filters={"creation": (">=", seven_days_ago), "docstatus": 1},
            fields=["name", "customer_name", "transaction_type", "points", "creation", "owner"],
            order_by="creation desc", limit=5,
        ):
            sign = "+" if lt.points > 0 else ""
            activity.append({
                "type": "loyalty",
                "description": f"Loyalty {lt.transaction_type}: {lt.customer_name}",
                "detail": f"{sign}{lt.points} pts",
                "timestamp": str(lt.creation),
                "user": lt.owner,
                "link": f"/app/ch-loyalty-transaction/{lt.name}",
            })

    # Service Requests — company-filtered
    if frappe.db.exists("DocType", "Service Request"):
        sr_filters = {"creation": (">=", seven_days_ago)}
        if company:
            sr_filters["company"] = company
        for sr in frappe.get_all(
            "Service Request", filters=sr_filters,
            fields=["name", "customer_name", "status", "company", "creation", "owner"],
            order_by="creation desc", limit=5,
        ):
            activity.append({
                "type": "service",
                "description": f"Service: {sr.customer_name} ({sr.status})",
                "detail": sr.company or "",
                "timestamp": str(sr.creation),
                "user": sr.owner,
                "link": f"/app/service-request/{sr.name}",
            })

    # Buyback — ALWAYS common (no company field)
    if frappe.db.exists("DocType", "Buyback Request"):
        for bb in frappe.get_all(
            "Buyback Request",
            filters={"creation": (">=", seven_days_ago)},
            fields=["name", "customer_name", "mobile_no", "buyback_price", "deal_status", "creation", "owner"],
            order_by="creation desc", limit=5,
        ):
            activity.append({
                "type": "buyback",
                "description": f"Buyback: {bb.customer_name or bb.mobile_no}",
                "detail": bb.deal_status or "",
                "amount": bb.buyback_price,
                "timestamp": str(bb.creation),
                "user": bb.owner,
                "link": f"/app/buyback-request/{bb.name}",
            })

    activity.sort(key=lambda x: x["timestamp"], reverse=True)
    return activity[:25]


# ── Revenue Trend ────────────────────────────────────────────────────────────

def _get_revenue_trend(today, company):
    """Monthly revenue + acquisition trend (6 months). Revenue = company-filtered."""
    cond = _co_cond(company, "si")
    p_base = _co_params(company)
    months = []

    for i in range(5, -1, -1):
        d = add_months(today, -i)
        ms = str(get_first_day(d))
        me = str(get_last_day(d))
        label = getdate(d).strftime("%b %Y")

        p = {"start": ms, "end": me, **p_base}

        revenue = frappe.db.sql(f"""
            SELECT IFNULL(SUM(si.grand_total), 0) FROM `tabSales Invoice` si
            WHERE si.docstatus = 1 AND si.posting_date BETWEEN %(start)s AND %(end)s {cond}
        """, p)[0][0]

        transactions = frappe.db.sql(f"""
            SELECT COUNT(*) FROM `tabSales Invoice` si
            WHERE si.docstatus = 1 AND si.posting_date BETWEEN %(start)s AND %(end)s {cond}
        """, p)[0][0]

        new_custs = frappe.db.count("Customer", {
            "creation": ("between", [ms, me + " 23:59:59"]),
            "disabled": 0,
        })

        months.append({
            "month": label,
            "revenue": flt(revenue),
            "new_customers": cint(new_custs),
            "transactions": cint(transactions),
        })

    return months


# ── Referral Stats — ALWAYS COMMON ───────────────────────────────────────────

def _get_referral_stats():
    """Referral program performance. ALWAYS global — customer-level."""
    total_referrers = frappe.db.count("Customer", {"ch_referral_code": ("is", "set"), "disabled": 0})
    total_referred = frappe.db.count("Customer", {"ch_referred_by": ("is", "set"), "disabled": 0})

    top_referrers = frappe.db.sql("""
        SELECT c.name as referrer, c.customer_name, COUNT(r.name) as referral_count
        FROM `tabCustomer` c
        INNER JOIN `tabCustomer` r ON r.ch_referred_by = c.name
        WHERE c.disabled = 0 AND r.disabled = 0
        GROUP BY c.name ORDER BY referral_count DESC LIMIT 5
    """, as_dict=True)

    return {
        "total_referrers": cint(total_referrers),
        "total_referred": cint(total_referred),
        "conversion_rate": round(total_referred / max(total_referrers, 1) * 100, 1),
        "top_referrers": top_referrers,
    }


# ── Store Performance ───────────────────────────────────────────────────────

def _get_store_performance(today, company):
    """Per-store engagement. Company-filtered when applicable."""
    cond = ""
    p = {}
    if company:
        cond = " AND sv.company = %(company)s"
        p = {"company": company}

    return frappe.db.sql(f"""
        SELECT
            sv.store, sv.company,
            COUNT(*) as total_visits,
            COUNT(DISTINCT sv.parent) as unique_customers,
            COUNT(DISTINCT CASE WHEN sv.visit_type = 'Purchase' THEN sv.parent END) as purchasers,
            COUNT(DISTINCT CASE WHEN sv.visit_type = 'Service' THEN sv.parent END) as service_customers,
            COUNT(DISTINCT CASE WHEN sv.visit_type = 'Buyback' THEN sv.parent END) as buyback_customers,
            MAX(sv.visit_date) as last_visit
        FROM `tabCH Customer Store Visit` sv
        WHERE sv.store IS NOT NULL AND sv.store != '' {cond}
        GROUP BY sv.store, sv.company
        ORDER BY total_visits DESC LIMIT 15
    """, p, as_dict=True)
