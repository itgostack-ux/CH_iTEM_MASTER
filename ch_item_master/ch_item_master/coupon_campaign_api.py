# Copyright (c) 2026, GoStack and contributors
# Campaign Hub API — backend for the Campaign Hub dashboard page.

import frappe
from frappe import _
from frappe.utils import flt, cint, getdate, nowdate, add_days


@frappe.whitelist()
def get_campaign_hub_data(company=None, from_date=None, to_date=None):
	"""Return all data needed by the Campaign Hub page."""
	filters = _build_filters(company)
	date_filters = _build_date_filters(from_date, to_date)

	return {
		"kpis": _get_kpis(filters),
		"pipeline": _get_pipeline(filters),
		"active_campaigns": _get_active_campaigns(filters),
		"top_campaigns": _get_top_campaigns(filters),
		"recent_redemptions": _get_recent_redemptions(filters, date_filters),
		"expiring_soon": _get_expiring_soon(filters),
		"insights": _get_insights(filters),
	}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_filters(company=None):
	f = {"docstatus": 1}
	if company:
		f["company"] = company
	return f


def _build_date_filters(from_date=None, to_date=None):
	if not from_date:
		from_date = add_days(nowdate(), -30)
	if not to_date:
		to_date = nowdate()
	return {"from_date": from_date, "to_date": to_date}


# ── KPIs ─────────────────────────────────────────────────────────────────────

def _get_kpis(filters):
	company_cond = "AND cc.company = %s" if filters.get("company") else ""
	params = [filters["company"]] if filters.get("company") else []

	row = frappe.db.sql(f"""
		SELECT
			COUNT(*) as total_campaigns,
			SUM(CASE WHEN cc.status = 'Active' THEN 1 ELSE 0 END) as active_campaigns,
			COALESCE(SUM(cc.total_codes_generated), 0) as total_codes,
			COALESCE(SUM(cc.total_redeemed), 0) as total_redeemed,
			COALESCE(SUM(cc.total_discount_given), 0) as total_discount,
			COALESCE(SUM(cc.total_revenue_generated), 0) as total_revenue
		FROM `tabCH Coupon Campaign` cc
		WHERE cc.docstatus = 1 {company_cond}
	""", params, as_dict=True)[0]

	total_codes = cint(row.total_codes)
	total_redeemed = cint(row.total_redeemed)

	return {
		"active_campaigns": cint(row.active_campaigns),
		"total_campaigns": cint(row.total_campaigns),
		"total_codes_in_circulation": total_codes,
		"total_redeemed": total_redeemed,
		"overall_redemption_rate": round(total_redeemed / total_codes * 100, 1) if total_codes else 0,
		"total_discount_given": flt(row.total_discount),
		"total_revenue": flt(row.total_revenue),
	}


# ── Pipeline ─────────────────────────────────────────────────────────────────

def _get_pipeline(filters):
	company_cond = "AND company = %s" if filters.get("company") else ""
	params = [filters["company"]] if filters.get("company") else []

	rows = frappe.db.sql(f"""
		SELECT status, COUNT(*) as count
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 {company_cond}
		GROUP BY status ORDER BY FIELD(status, 'Draft','Active','Paused','Completed','Expired','Cancelled')
	""", params, as_dict=True)

	return [{"status": r.status, "count": cint(r["count"])} for r in rows]


# ── Active Campaigns ─────────────────────────────────────────────────────────

def _get_active_campaigns(filters):
	company_cond = "AND company = %s" if filters.get("company") else ""
	params = [filters["company"]] if filters.get("company") else []

	return frappe.db.sql(f"""
		SELECT
			name, campaign_name, campaign_type, company,
			valid_from, valid_upto, status,
			total_codes_generated, total_distributed, total_redeemed,
			total_discount_given, total_revenue_generated, redemption_rate
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status IN ('Active', 'Paused') {company_cond}
		ORDER BY creation DESC
		LIMIT 30
	""", params, as_dict=True)


# ── Top Campaigns by Revenue ─────────────────────────────────────────────────

def _get_top_campaigns(filters):
	company_cond = "AND company = %s" if filters.get("company") else ""
	params = [filters["company"]] if filters.get("company") else []

	return frappe.db.sql(f"""
		SELECT
			name, campaign_name, campaign_type, status,
			total_codes_generated, total_redeemed,
			total_discount_given, total_revenue_generated, redemption_rate
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND total_redeemed > 0 {company_cond}
		ORDER BY total_revenue_generated DESC
		LIMIT 10
	""", params, as_dict=True)


# ── Recent Redemptions ───────────────────────────────────────────────────────

def _get_recent_redemptions(filters, date_filters):
	"""Get recent coupon/voucher redemptions across all campaigns."""
	company_cond = "AND pi.company = %(company)s" if filters.get("company") else ""
	if filters.get("company"):
		date_filters["company"] = filters["company"]

	# Coupon redemptions from POS Invoice
	coupon_redeem = frappe.db.sql(f"""
		SELECT
			pi.name as invoice, pi.posting_date, pi.grand_total,
			pi.discount_amount, pi.coupon_code as code_ref,
			'Coupon' as instrument_type,
			cc.coupon_code as code_used,
			camp.campaign_name
		FROM `tabPOS Invoice` pi
		JOIN `tabCoupon Code` cc ON cc.name = pi.coupon_code
		LEFT JOIN `tabCH Campaign Code` ccode ON ccode.reference_name = cc.name
			AND ccode.reference_doctype = 'Coupon Code'
		LEFT JOIN `tabCH Coupon Campaign` camp ON camp.name = ccode.parent
		WHERE pi.docstatus = 1
			AND pi.coupon_code IS NOT NULL AND pi.coupon_code != ''
			AND pi.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{company_cond}
		ORDER BY pi.posting_date DESC
		LIMIT 20
	""", date_filters, as_dict=True)

	# Voucher redemptions from CH Voucher Transaction
	voucher_redeem = frappe.db.sql(f"""
		SELECT
			vt.pos_invoice as invoice,
			DATE(vt.transaction_date) as posting_date,
			ABS(vt.amount) as discount_amount,
			v.voucher_code as code_used,
			'Voucher' as instrument_type,
			camp.campaign_name
		FROM `tabCH Voucher Transaction` vt
		JOIN `tabCH Voucher` v ON v.name = vt.parent
		LEFT JOIN `tabCH Campaign Code` ccode ON ccode.reference_name = v.name
			AND ccode.reference_doctype = 'CH Voucher'
		LEFT JOIN `tabCH Coupon Campaign` camp ON camp.name = ccode.parent
		WHERE vt.transaction_type = 'Redeem'
			AND DATE(vt.transaction_date) BETWEEN %(from_date)s AND %(to_date)s
		ORDER BY vt.transaction_date DESC
		LIMIT 20
	""", date_filters, as_dict=True)

	combined = coupon_redeem + voucher_redeem
	combined.sort(key=lambda x: str(x.get("posting_date", "")), reverse=True)
	return combined[:30]


# ── Expiring Soon ────────────────────────────────────────────────────────────

def _get_expiring_soon(filters):
	company_cond = "AND company = %(company)s" if filters.get("company") else ""
	soon = add_days(nowdate(), 7)
	params = {"soon": soon}
	if filters.get("company"):
		params["company"] = filters["company"]

	return frappe.db.sql(f"""
		SELECT
			name, campaign_name, campaign_type, valid_upto,
			total_codes_generated, total_redeemed, redemption_rate
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND valid_upto BETWEEN CURDATE() AND %(soon)s
			{company_cond}
		ORDER BY valid_upto ASC
		LIMIT 10
	""", params, as_dict=True)


# ── AI Insights ──────────────────────────────────────────────────────────────

def _get_insights(filters):
	insights = []
	company_cond = "AND company = %(company)s" if filters.get("company") else ""
	params = {}
	if filters.get("company"):
		params["company"] = filters["company"]

	# 1. Low redemption campaigns
	low_redeem = frappe.db.sql(f"""
		SELECT campaign_name, redemption_rate, total_codes_generated
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND total_codes_generated > 0
			AND redemption_rate < 5
			AND DATEDIFF(CURDATE(), valid_from) > 7
			{company_cond}
		ORDER BY redemption_rate ASC LIMIT 3
	""", params, as_dict=True)
	for c in low_redeem:
		insights.append({
			"type": "warning",
			"icon": "📉",
			"text": f"<b>{c.campaign_name}</b> has only {c.redemption_rate:.1f}% redemption after 7+ days. "
					f"Consider boosting distribution or adjusting the offer.",
		})

	# 2. Expiring soon with unused codes
	soon = add_days(nowdate(), 3)
	exp_params = {"soon": soon, **params}
	expiring = frappe.db.sql(f"""
		SELECT campaign_name, valid_upto,
			total_codes_generated - total_redeemed as unused
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND valid_upto BETWEEN CURDATE() AND %(soon)s
			AND total_codes_generated > total_redeemed
			{company_cond}
		LIMIT 3
	""", exp_params, as_dict=True)
	for c in expiring:
		insights.append({
			"type": "alert",
			"icon": "⏰",
			"text": f"<b>{c.campaign_name}</b> expires on {c.valid_upto} with "
					f"<b>{c.unused}</b> unused codes. Push a reminder blast.",
		})

	# 3. High performers
	top = frappe.db.sql(f"""
		SELECT campaign_name, redemption_rate, total_revenue_generated
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND redemption_rate > 30 AND total_redeemed >= 5
			{company_cond}
		ORDER BY total_revenue_generated DESC LIMIT 2
	""", params, as_dict=True)
	for c in top:
		insights.append({
			"type": "success",
			"icon": "🏆",
			"text": f"<b>{c.campaign_name}</b> is performing well at "
					f"{c.redemption_rate:.1f}% redemption, generating "
					f"₹{c.total_revenue_generated:,.0f} in revenue.",
		})

	# 4. No active campaigns
	active_count = frappe.db.count("CH Coupon Campaign",
		{"docstatus": 1, "status": "Active", **({"company": filters["company"]} if filters.get("company") else {})})
	if active_count == 0:
		insights.append({
			"type": "info",
			"icon": "💡",
			"text": "No active campaigns. Create a new campaign to start driving traffic.",
		})

	return insights


# ── Code Lookup ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def lookup_code(code):
	"""Look up a coupon or voucher code and return its campaign + status."""
	if not code:
		return {"found": False}

	# Check Coupon Code
	coupon = frappe.db.get_value("Coupon Code", {"coupon_code": code},
		["name", "coupon_code", "coupon_type", "pricing_rule", "used", "maximum_use",
		 "valid_from", "valid_upto"], as_dict=True)
	if coupon:
		campaign = frappe.db.get_value("CH Campaign Code",
			{"reference_doctype": "Coupon Code", "reference_name": coupon.name},
			["parent", "status"], as_dict=True)
		campaign_name = None
		if campaign:
			campaign_name = frappe.db.get_value("CH Coupon Campaign", campaign.parent, "campaign_name")
		return {
			"found": True,
			"instrument_type": "Coupon",
			"code": coupon.coupon_code,
			"reference": coupon.name,
			"used": coupon.used,
			"maximum_use": coupon.maximum_use,
			"valid_from": coupon.valid_from,
			"valid_upto": coupon.valid_upto,
			"campaign": campaign_name,
			"campaign_id": campaign.parent if campaign else None,
		}

	# Check CH Voucher
	voucher = frappe.db.get_value("CH Voucher", {"voucher_code": code},
		["name", "voucher_code", "voucher_type", "status", "original_amount",
		 "balance", "valid_from", "valid_upto", "issued_to_name"], as_dict=True)
	if voucher:
		campaign = frappe.db.get_value("CH Campaign Code",
			{"reference_doctype": "CH Voucher", "reference_name": voucher.name},
			["parent", "status"], as_dict=True)
		campaign_name = None
		if campaign:
			campaign_name = frappe.db.get_value("CH Coupon Campaign", campaign.parent, "campaign_name")
		return {
			"found": True,
			"instrument_type": "Voucher",
			"code": voucher.voucher_code,
			"reference": voucher.name,
			"voucher_type": voucher.voucher_type,
			"status": voucher.status,
			"original_amount": voucher.original_amount,
			"balance": voucher.balance,
			"valid_from": voucher.valid_from,
			"valid_upto": voucher.valid_upto,
			"issued_to": voucher.issued_to_name,
			"campaign": campaign_name,
			"campaign_id": campaign.parent if campaign else None,
		}

	return {"found": False}
