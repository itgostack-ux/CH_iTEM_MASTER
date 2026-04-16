"""VAS Hub – Backend API for warranty plans, claims, and vouchers dashboard."""

import frappe
from frappe.utils import flt, nowdate, get_first_day, cint, getdate, add_days


def _build_filters(company=None, store=None, from_date=None, to_date=None):
    prm = {}
    co_sp = ""
    co_wc = ""
    co_v = ""
    if company:
        co_sp = " AND sp.company = %(company)s"
        co_wc = " AND wc.company = %(company)s"
        co_v = " AND v.company = %(company)s"
        prm["company"] = company
    # CH Sold Plan has no store field; CH Warranty Claim has reported_at_store
    wh_wc = ""
    if store:
        wh_wc = " AND wc.reported_at_store = %(store)s"
        prm["store"] = store
    from_date = str(getdate(from_date)) if from_date else None
    to_date = str(getdate(to_date)) if to_date else None
    if from_date:
        prm["from_date"] = from_date
    if to_date:
        prm["to_date"] = to_date

    def date_col(col):
        if from_date and to_date:
            return f" AND {col} BETWEEN %(from_date)s AND %(to_date)s"
        if from_date:
            return f" AND {col} >= %(from_date)s"
        if to_date:
            return f" AND {col} <= %(to_date)s"
        return ""

    return {"prm": prm, "co_sp": co_sp, "co_wc": co_wc, "co_v": co_v,
            "wh_wc": wh_wc, "date_col": date_col}


@frappe.whitelist()
def get_vas_hub_data(company=None, store=None, from_date=None, to_date=None):
    """VAS dashboard: Warranty Plans → Sold Plans → Claims → Vouchers."""
    f = _build_filters(company, store, from_date, to_date)
    prm = f["prm"]
    co_sp = f["co_sp"]
    co_wc = f["co_wc"]
    co_v = f["co_v"]
    wh_wc = f["wh_wc"]
    dc = f["date_col"]

    today = nowdate()
    first_day = get_first_day(today)
    prm["today"] = today
    prm["first_day"] = str(first_day)
    prm["thirty_days"] = str(add_days(today, 30))

    # Note: Sold plans may not have company/store — use co_sp and wh_sp that reference sp alias
    # For claims and vouchers, we join via sold_plan if needed

    # ── Pipeline — Sold Plans by status ──
    plan_counts = frappe.db.sql(
        f"""SELECT sp.status, COUNT(*) AS cnt
            FROM `tabCH Sold Plan` sp
            WHERE 1=1 {co_sp} {dc('sp.creation')}
            GROUP BY sp.status""", prm, as_dict=True
    )
    pc = {r.status: cint(r.cnt) for r in plan_counts}

    # Claims by claim_status
    claim_counts = frappe.db.sql(
        f"""SELECT wc.claim_status, COUNT(*) AS cnt
            FROM `tabCH Warranty Claim` wc
            WHERE 1=1 {co_wc} {wh_wc} {dc('wc.claim_date')}
            GROUP BY wc.claim_status""", prm, as_dict=True
    )
    cc = {r.claim_status: cint(r.cnt) for r in claim_counts}

    # Voucher counts
    voucher_counts = frappe.db.sql(
        f"""SELECT v.status, COUNT(*) AS cnt
            FROM `tabCH Voucher` v
            WHERE 1=1 {co_v} {dc('v.creation')}
            GROUP BY v.status""", prm, as_dict=True
    )
    vc = {r.status: cint(r.cnt) for r in voucher_counts}

    pipeline = [
        {"key": "plan_active",  "label": "Active Plans",    "count": pc.get("Active", 0),
         "icon": "certificate",  "color": "#059669",  "sub": "Currently covered"},
        {"key": "plan_expired", "label": "Expired Plans",   "count": pc.get("Expired", 0),
         "icon": "clock-o",      "color": "#f59e0b",  "sub": "Coverage ended"},
        {"key": "plan_claimed", "label": "Claimed Plans",   "count": pc.get("Claimed", 0),
         "icon": "check-circle", "color": "#3b82f6",  "sub": "Claim filed"},
        {"key": "claims_open",  "label": "Open Claims",     "count": cc.get("Pending Approval", 0) + cc.get("Ticket Created", 0),
         "icon": "gavel",        "color": "#7c3aed",  "sub": f"Pending: {cc.get('Pending Approval', 0)} · Ticket: {cc.get('Ticket Created', 0)}"},
        {"key": "vouchers_active", "label": "Active Vouchers", "count": vc.get("Active", 0),
         "icon": "ticket",       "color": "#d946ef",  "sub": f"Used: {vc.get('Fully Used', 0)}"},
    ]

    # ── KPIs ──
    total_plans = sum(pc.values())
    active_plans = pc.get("Active", 0)
    total_claims = sum(cc.values())
    total_vouchers = sum(vc.values())

    plans_sold_mtd = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabCH Sold Plan` sp
            WHERE sp.creation BETWEEN %(first_day)s AND %(today)s
            {co_sp}""", prm
    )[0][0]

    # VAS revenue from sold plans (plan_price field)
    vas_rev = frappe.db.sql(
        f"""SELECT COALESCE(SUM(sp.plan_price), 0) FROM `tabCH Sold Plan` sp
            WHERE sp.creation BETWEEN %(first_day)s AND %(today)s
            {co_sp}""", prm
    )[0][0]

    # Voucher totals: original_amount and balance
    voucher_total = frappe.db.sql(
        f"""SELECT COALESCE(SUM(v.original_amount), 0) FROM `tabCH Voucher` v
            WHERE 1=1 {co_v} {dc('v.creation')}""", prm
    )[0][0]
    voucher_balance = frappe.db.sql(
        f"""SELECT COALESCE(SUM(v.balance), 0) FROM `tabCH Voucher` v
            WHERE 1=1 {co_v} {dc('v.creation')}""", prm
    )[0][0]
    voucher_redeemed = flt(voucher_total) - flt(voucher_balance)
    voucher_util = f"{flt(voucher_redeemed)*100/max(flt(voucher_total),1):.0f}%"
    claim_rate = f"{total_claims*100//max(total_plans,1)}%" if total_plans else "0%"

    kpis = [
        {"key": "active_plans",  "label": "Active Plans",       "value": active_plans,          "color": "#059669", "fmt": "number"},
        {"key": "sold_mtd",      "label": "Sold This Month",    "value": cint(plans_sold_mtd),  "color": "#3b82f6", "fmt": "number"},
        {"key": "total_claims",  "label": "Total Claims",       "value": total_claims,          "color": "#7c3aed", "fmt": "number"},
        {"key": "claim_rate",    "label": "Claim Rate",         "value": claim_rate,            "color": "#f59e0b", "fmt": "text"},
        {"key": "active_vouchers","label": "Active Vouchers",   "value": vc.get("Active", 0),   "color": "#d946ef", "fmt": "number"},
        {"key": "voucher_util",  "label": "Voucher Utilization","value": voucher_util,          "color": "#0ea5e9", "fmt": "text"},
        {"key": "vas_rev",       "label": "VAS Revenue MTD",    "value": flt(vas_rev),          "color": "#10b981", "fmt": "currency"},
        {"key": "total_plans",   "label": "Total Plans Sold",   "value": total_plans,           "color": "#6366f1", "fmt": "number"},
    ]

    # ── Detail tables ──
    sold_plans = frappe.db.sql(
        f"""SELECT sp.name, sp.customer_name, sp.customer, sp.warranty_plan,
                   sp.start_date, sp.end_date, sp.status, sp.plan_price
            FROM `tabCH Sold Plan` sp
            WHERE 1=1 {co_sp} {dc('sp.creation')}
            ORDER BY sp.creation DESC LIMIT 50""", prm, as_dict=True
    )

    warranty_claims = frappe.db.sql(
        f"""SELECT wc.name, wc.customer_name, wc.customer, wc.sold_plan,
                   wc.claim_date, wc.claim_status, wc.approved_amount, wc.total_claim_cost
            FROM `tabCH Warranty Claim` wc
            WHERE 1=1 {co_wc} {wh_wc} {dc('wc.claim_date')}
            ORDER BY wc.claim_date DESC LIMIT 50""", prm, as_dict=True
    )

    vouchers = frappe.db.sql(
        f"""SELECT v.name, v.issued_to_name, v.issued_to, v.status,
                   v.original_amount, v.balance, v.valid_upto, v.voucher_type
            FROM `tabCH Voucher` v
            WHERE 1=1 {co_v} {dc('v.creation')}
            ORDER BY v.creation DESC LIMIT 50""", prm, as_dict=True
    )

    expiring_soon = frappe.db.sql(
        f"""SELECT sp.name, sp.customer_name, sp.customer, sp.warranty_plan,
                   sp.end_date,
                   DATEDIFF(sp.end_date, %(today)s) AS days_left
            FROM `tabCH Sold Plan` sp
            WHERE sp.status = 'Active'
            AND sp.end_date BETWEEN %(today)s AND %(thirty_days)s
            {co_sp}
            ORDER BY sp.end_date ASC LIMIT 30""", prm, as_dict=True
    )

    plan_performance = frappe.db.sql(
        f"""SELECT sp.warranty_plan,
                   COUNT(*) AS total,
                   SUM(CASE WHEN sp.status='Active' THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN sp.status='Claimed' THEN 1 ELSE 0 END) AS claimed
            FROM `tabCH Sold Plan` sp
            WHERE 1=1 {co_sp} {dc('sp.creation')}
            GROUP BY sp.warranty_plan
            ORDER BY total DESC LIMIT 20""", prm, as_dict=True
    )
    for p in plan_performance:
        p["claim_rate"] = f"{cint(p.get('claimed',0))*100//max(cint(p.get('total',1)),1)}%"

    # ── AI Insights ──
    ai_insights = []
    expiring_count = len(expiring_soon)
    if expiring_count > 5:
        ai_insights.append({
            "severity": "High", "title": f"{expiring_count} Plans Expiring in 30 Days",
            "detail": "Opportunity for renewal outreach before coverage lapses.",
            "action": "Contact customers with expiring plans for renewal offers."
        })
    pending_claims = cc.get("Pending Approval", 0)
    if pending_claims > 3:
        ai_insights.append({
            "severity": "High", "title": f"{pending_claims} Claims Pending Approval",
            "detail": "Claims awaiting approval may delay customer resolution.",
            "action": "Review and approve/reject pending claims promptly."
        })
    if flt(voucher_redeemed) / max(flt(voucher_total), 1) < 0.3 and flt(voucher_total) > 0:
        ai_insights.append({
            "severity": "Medium", "title": "Low Voucher Utilization",
            "detail": f"Only {voucher_util} of voucher value has been redeemed.",
            "action": "Remind customers about their available voucher balance."
        })
    if not ai_insights:
        ai_insights.append({
            "severity": "Low", "title": "VAS Operations Running Smoothly",
            "detail": "No significant issues detected in warranty and voucher operations.",
        })

    financial_control = {
        "active_plans": active_plans,
        "claim_rate": claim_rate,
        "voucher_utilization": voucher_util,
        "vas_revenue_mtd": flt(vas_rev),
    }

    return {
        "pipeline": pipeline,
        "kpis": kpis,
        "sold_plans": sold_plans,
        "warranty_claims": warranty_claims,
        "vouchers": vouchers,
        "expiring_soon": expiring_soon,
        "plan_performance": plan_performance,
        "ai_insights": ai_insights,
        "financial_control": financial_control,
    }
