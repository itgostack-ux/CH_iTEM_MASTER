# Copyright (c) 2026, GoStack and contributors
# Campaign Hub API — backend for the Campaign Hub dashboard page.

import frappe
from frappe import _
from frappe.utils import add_days, cint, escape_html, flt, getdate, nowdate

from ch_item_master.config import get_int_setting, is_privileged_user, require_role_setting
from ch_item_master.security import get_company_scope


_CAMPAIGN_ROLES = ("Sales Manager", "CH Master Manager")
_HUB_READ_DOCTYPES = (
	"CH Coupon Campaign",
	"POS Invoice",
	"Sales Invoice",
	"Coupon Code",
	"CH Voucher",
)
_LOOKUP_READ_DOCTYPES = ("CH Coupon Campaign", "Coupon Code", "CH Voucher")
_MAX_DATE_RANGE_DAYS = 366
_MAX_CODE_LENGTH = 140


@frappe.whitelist()
def get_campaign_hub_data(company=None, from_date=None, to_date=None):
	"""Return all data needed by the Campaign Hub page."""
	_require_campaign_access(_HUB_READ_DOCTYPES)
	company = _resolve_company(company)
	filters = _build_filters(company)
	date_filters = _build_date_filters(from_date, to_date)
	invoice_scope = _build_invoice_scope()

	return {
		"kpis": _get_kpis(filters),
		"pipeline": _get_pipeline(filters),
		"active_campaigns": _get_active_campaigns(filters),
		"top_campaigns": _get_top_campaigns(filters),
		"recent_redemptions": _get_recent_redemptions(filters, date_filters, invoice_scope),
		"expiring_soon": _get_expiring_soon(filters),
		"insights": _get_insights(filters),
		"display_thresholds": {
			"high_redemption_rate": get_int_setting("coupon_high_redemption_percent", 30),
			"medium_redemption_rate": get_int_setting("coupon_medium_redemption_percent", 10),
		},
	}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _require_campaign_access(doctypes):
	require_role_setting(
		"coupon_campaign_management_roles",
		_CAMPAIGN_ROLES,
		action="view campaign and redemption data",
	)
	if is_privileged_user():
		return

	for doctype in doctypes:
		if not frappe.has_permission(doctype, ptype="read", user=frappe.session.user):
			frappe.throw(
				_("You do not have read permission for {0}.").format(doctype),
				frappe.PermissionError,
			)


def _resolve_company(company=None):
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
			frappe.throw(_("Select a company before opening Campaign Hub."), frappe.ValidationError)
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
			msg=_("You are not permitted to view campaigns for this company."),
		)

	return company


def _build_invoice_scope():
	if is_privileged_user():
		return {"clause": "", "params": {}}

	try:
		from ch_erp15.ch_erp15.scope import get_user_scope
	except (ImportError, ModuleNotFoundError):
		frappe.throw(
			_("Location scope validation is unavailable. Contact an administrator."),
			frappe.PermissionError,
		)

	scope = get_user_scope(frappe.session.user) or {}
	if scope.get("bypass"):
		return {"clause": "", "params": {}}

	warehouses = tuple(sorted(filter(None, scope.get("warehouses") or ())))
	stores = tuple(sorted(filter(None, scope.get("stores") or ())))
	profiles = ()
	if stores:
		profiles = tuple(sorted(filter(None, frappe.get_all(
			"CH Store",
			filters={"name": ("in", stores), "disabled": 0},
			pluck="pos_profile",
		))))

	clauses = []
	params = {}
	if profiles:
		clauses.append("{alias}.pos_profile IN %(scope_profiles)s")
		params["scope_profiles"] = profiles
	if warehouses:
		clauses.append("{alias}.set_warehouse IN %(scope_warehouses)s")
		params["scope_warehouses"] = warehouses

	if not clauses:
		return {"clause": " AND 1 = 0", "params": {}}
	return {"clause": " AND (" + " OR ".join(clauses) + ")", "params": params}


def _build_filters(company=None):
	f = {"docstatus": 1}
	if company:
		f["company"] = company
	return f


def _build_date_filters(from_date=None, to_date=None):
	try:
		end_date = getdate(to_date or nowdate())
		default_window_days = get_int_setting("coupon_default_window_days", 30, minimum=1)
		start_date = getdate(from_date or add_days(end_date, -default_window_days))
	except (TypeError, ValueError):
		frappe.throw(_("Enter valid From Date and To Date values."), frappe.ValidationError)

	if start_date > end_date:
		frappe.throw(_("From Date cannot be after To Date."), frappe.ValidationError)
	if (end_date - start_date).days > _MAX_DATE_RANGE_DAYS:
		frappe.throw(
			_("The Campaign Hub date range cannot exceed {0} days.").format(_MAX_DATE_RANGE_DAYS),
			frappe.ValidationError,
		)
	return {"from_date": start_date, "to_date": end_date}


# ── KPIs ─────────────────────────────────────────────────────────────────────

def _get_kpis(filters):
	cond = ["cc.docstatus = 1"]
	params = []
	if filters.get("company"):
		cond.append("cc.company = %s")
		params.append(filters["company"])

	row = frappe.db.sql(
		"""
		SELECT
			COUNT(*) as total_campaigns,
			SUM(CASE WHEN cc.status = 'Active' THEN 1 ELSE 0 END) as active_campaigns,
			COALESCE(SUM(cc.total_codes_generated), 0) as total_codes,
			COALESCE(SUM(cc.total_redeemed), 0) as total_redeemed,
			COALESCE(SUM(cc.total_discount_given), 0) as total_discount,
			COALESCE(SUM(cc.total_revenue_generated), 0) as total_revenue
		FROM `tabCH Coupon Campaign` cc
		WHERE """
		+ " AND ".join(cond),
		params,
		as_dict=True,
	)[0]

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
	cond = ["docstatus = 1"]
	params = []
	if filters.get("company"):
		cond.append("company = %s")
		params.append(filters["company"])

	rows = frappe.db.sql(
		"""
		SELECT status, COUNT(*) as count
		FROM `tabCH Coupon Campaign`
		WHERE """
		+ " AND ".join(cond)
		+ " GROUP BY status ORDER BY FIELD(status, 'Draft','Active','Paused','Completed','Expired','Cancelled')",
		params,
		as_dict=True,
	)

	return [{"status": r.status, "count": cint(r["count"])} for r in rows]


# ── Active Campaigns ─────────────────────────────────────────────────────────

def _get_active_campaigns(filters):
	cond = ["docstatus = 1", "status IN ('Active', 'Paused')"]
	params = []
	if filters.get("company"):
		cond.append("company = %s")
		params.append(filters["company"])

	return frappe.db.sql(
		"""
		SELECT
			name, campaign_name, campaign_type, company,
			valid_from, valid_upto, status,
			total_codes_generated, total_distributed, total_redeemed,
			total_discount_given, total_revenue_generated, redemption_rate
		FROM `tabCH Coupon Campaign`
		WHERE """
		+ " AND ".join(cond)
		+ " ORDER BY creation DESC LIMIT 30",
		params,
		as_dict=True,
	)


# ── Top Campaigns by Revenue ─────────────────────────────────────────────────

def _get_top_campaigns(filters):
	cond = ["docstatus = 1", "total_redeemed > 0"]
	params = []
	if filters.get("company"):
		cond.append("company = %s")
		params.append(filters["company"])

	return frappe.db.sql(
		"""
		SELECT
			name, campaign_name, campaign_type, status,
			total_codes_generated, total_redeemed,
			total_discount_given, total_revenue_generated, redemption_rate
		FROM `tabCH Coupon Campaign`
		WHERE """
		+ " AND ".join(cond)
		+ " ORDER BY total_revenue_generated DESC LIMIT 10",
		params,
		as_dict=True,
	)


# ── Recent Redemptions ───────────────────────────────────────────────────────

def _get_recent_redemptions(filters, date_filters, invoice_scope):
	"""Get recent coupon/voucher redemptions across all campaigns."""
	date_filters = dict(date_filters)
	if filters.get("company"):
		date_filters["company"] = filters["company"]
	date_filters.update(invoice_scope["params"])

	pos_company_clause = ""
	voucher_company_clause = ""
	if filters.get("company"):
		pos_company_clause = """
			AND pi.company = %(company)s
			AND (camp.name IS NULL OR camp.company = %(company)s)
		"""
		voucher_company_clause = """
			AND v.company = %(company)s
			AND (si.name IS NULL OR si.company = %(company)s)
			AND (camp.name IS NULL OR camp.company = %(company)s)
		"""

	# Coupon redemptions from POS Invoice
	coupon_redeem = frappe.db.sql(
		"""
		SELECT
			pi.name as invoice, pi.posting_date, pi.grand_total,
			pi.discount_amount, pi.coupon_code as code_ref,
			'POS Invoice' as invoice_doctype,
			'Coupon' as instrument_type,
			cc.coupon_code as code_used,
			camp.campaign_name
		FROM `tabPOS Invoice` pi
		JOIN `tabCoupon Code` cc ON cc.name = pi.coupon_code
		LEFT JOIN `tabCH Campaign Code` ccode ON ccode.reference_name = cc.name
			AND ccode.reference_doctype = 'Coupon Code'
			AND ccode.parenttype = 'CH Coupon Campaign'
			AND ccode.parentfield = 'codes'
		LEFT JOIN `tabCH Coupon Campaign` camp ON camp.name = ccode.parent
		WHERE pi.docstatus = 1
			AND pi.coupon_code IS NOT NULL AND pi.coupon_code != ''
			AND pi.posting_date BETWEEN %(from_date)s AND %(to_date)s
			AND (camp.name IS NULL OR camp.company = pi.company)
		"""
		+ pos_company_clause
		+ invoice_scope["clause"].format(alias="pi")
		+ " ORDER BY pi.posting_date DESC LIMIT 20",
		date_filters,
		as_dict=True,
	)

	# Voucher redemptions from CH Voucher Transaction
	voucher_redeem = frappe.db.sql(
		"""
		SELECT
			vt.pos_invoice as invoice,
			DATE(vt.transaction_date) as posting_date,
			ABS(vt.amount) as discount_amount,
			v.voucher_code as code_used,
			'Sales Invoice' as invoice_doctype,
			'Voucher' as instrument_type,
			camp.campaign_name
		FROM `tabCH Voucher Transaction` vt
		JOIN `tabCH Voucher` v ON v.name = vt.parent
			AND vt.parenttype = 'CH Voucher' AND vt.parentfield = 'transactions'
		LEFT JOIN `tabSales Invoice` si ON si.name = vt.pos_invoice AND si.docstatus = 1
		LEFT JOIN `tabCH Campaign Code` ccode ON ccode.reference_name = v.name
			AND ccode.reference_doctype = 'CH Voucher'
			AND ccode.parenttype = 'CH Coupon Campaign'
			AND ccode.parentfield = 'codes'
		LEFT JOIN `tabCH Coupon Campaign` camp ON camp.name = ccode.parent
		WHERE vt.transaction_type = 'Redeem'
			AND DATE(vt.transaction_date) BETWEEN %(from_date)s AND %(to_date)s
			AND ((vt.pos_invoice IS NULL OR vt.pos_invoice = '') OR si.name IS NOT NULL)
			AND (si.name IS NULL OR v.company = si.company)
			AND (camp.name IS NULL OR camp.company = v.company)
		"""
		+ voucher_company_clause
		+ invoice_scope["clause"].format(alias="si")
		+ " ORDER BY vt.transaction_date DESC LIMIT 20",
		date_filters,
		as_dict=True,
	)

	combined = coupon_redeem + voucher_redeem
	combined.sort(key=lambda x: str(x.get("posting_date", "")), reverse=True)
	return combined[:30]


# ── Expiring Soon ────────────────────────────────────────────────────────────

def _get_expiring_soon(filters):
	expiring_days = get_int_setting("coupon_expiring_soon_days", 7)
	soon = add_days(nowdate(), expiring_days)
	params = {"soon": soon}
	company_clause = ""
	if filters.get("company"):
		params["company"] = filters["company"]
		company_clause = "AND company = %(company)s"

	return frappe.db.sql(
		"""
		SELECT
			name, campaign_name, campaign_type, valid_upto,
			total_codes_generated, total_redeemed, redemption_rate
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND valid_upto BETWEEN CURDATE() AND %(soon)s
		"""
		+ company_clause
		+ " ORDER BY valid_upto ASC LIMIT 10",
		params,
		as_dict=True,
	)


# ── AI Insights ──────────────────────────────────────────────────────────────

def _get_insights(filters):
	insights = []
	low_redemption_percent = get_int_setting("coupon_low_redemption_percent", 5)
	low_redemption_age_days = get_int_setting("coupon_low_redemption_min_age_days", 7)
	urgent_expiry_days = get_int_setting("coupon_urgent_expiry_days", 3)
	high_redemption_percent = get_int_setting("coupon_high_redemption_percent", 30)
	high_redemption_min_count = get_int_setting("coupon_high_redemption_min_count", 5)
	params = {
		"low_redemption_percent": low_redemption_percent,
		"low_redemption_age_days": low_redemption_age_days,
		"high_redemption_percent": high_redemption_percent,
		"high_redemption_min_count": high_redemption_min_count,
	}
	company_clause = ""
	if filters.get("company"):
		params["company"] = filters["company"]
		company_clause = "AND company = %(company)s"

	# 1. Low redemption campaigns
	low_redeem = frappe.db.sql(
		"""
		SELECT campaign_name, redemption_rate, total_codes_generated
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND total_codes_generated > 0
			AND redemption_rate < %(low_redemption_percent)s
			AND DATEDIFF(CURDATE(), valid_from) > %(low_redemption_age_days)s
		"""
		+ company_clause
		+ " ORDER BY redemption_rate ASC LIMIT 3",
		params,
		as_dict=True,
	)
	for c in low_redeem:
		campaign_name = escape_html(c.campaign_name or _("Unnamed campaign"))
		insights.append({
			"type": "warning",
			"icon": "📉",
			"text": (
				f"<b>{campaign_name}</b> has only {flt(c.redemption_rate):.1f}% redemption "
				f"after {low_redemption_age_days}+ days. "
				"Consider boosting distribution or adjusting the offer."
			),
		})

	# 2. Expiring soon with unused codes
	soon = add_days(nowdate(), urgent_expiry_days)
	exp_params = {"soon": soon, **params}
	expiring = frappe.db.sql(
		"""
		SELECT campaign_name, valid_upto,
			total_codes_generated - total_redeemed as unused
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND valid_upto BETWEEN CURDATE() AND %(soon)s
			AND total_codes_generated > total_redeemed
		"""
		+ company_clause
		+ " LIMIT 3",
		exp_params,
		as_dict=True,
	)
	for c in expiring:
		campaign_name = escape_html(c.campaign_name or _("Unnamed campaign"))
		insights.append({
			"type": "alert",
			"icon": "⏰",
			"text": f"<b>{campaign_name}</b> expires on {c.valid_upto} with "
					f"<b>{cint(c.unused)}</b> unused codes. Push a reminder blast.",
		})

	# 3. High performers
	top = frappe.db.sql(
		"""
		SELECT campaign_name, redemption_rate, total_revenue_generated
		FROM `tabCH Coupon Campaign`
		WHERE docstatus = 1 AND status = 'Active'
			AND redemption_rate > %(high_redemption_percent)s
			AND total_redeemed >= %(high_redemption_min_count)s
		"""
		+ company_clause
		+ " ORDER BY total_revenue_generated DESC LIMIT 2",
		params,
		as_dict=True,
	)
	for c in top:
		campaign_name = escape_html(c.campaign_name or _("Unnamed campaign"))
		insights.append({
			"type": "success",
			"icon": "🏆",
			"text": f"<b>{campaign_name}</b> is performing well at "
					f"{flt(c.redemption_rate):.1f}% redemption, generating "
					f"₹{flt(c.total_revenue_generated):,.0f} in revenue.",
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
	_require_campaign_access(_LOOKUP_READ_DOCTYPES)
	code = (code or "").strip()
	if not code:
		return {"found": False}
	if len(code) > _MAX_CODE_LENGTH:
		frappe.throw(_("The code value is too long."), frappe.ValidationError)

	coupon = frappe.db.get_value("Coupon Code", {"coupon_code": code},
		["name", "coupon_code", "coupon_type", "pricing_rule", "used", "maximum_use",
		 "valid_from", "valid_upto"], as_dict=True)
	if coupon:
		_require_document_read("Coupon Code", coupon.name)
		campaign = _get_linked_campaign("Coupon Code", coupon.name)
		pricing_rule = None
		if coupon.pricing_rule:
			pricing_rule = frappe.db.get_value(
				"Pricing Rule",
				coupon.pricing_rule,
				["company", "warehouse"],
				as_dict=True,
			)
		company = _consistent_company(
			campaign.get("company") if campaign else None,
			pricing_rule.get("company") if pricing_rule else None,
		)
		warehouse = pricing_rule.get("warehouse") if pricing_rule else None
		if warehouse:
			company = _consistent_company(
				company,
				frappe.db.get_value("Warehouse", warehouse, "company"),
			)
		_require_lookup_scope(company, warehouse=warehouse)
		return {
			"found": True,
			"instrument_type": "Coupon",
			"code": coupon.coupon_code,
			"reference": coupon.name,
			"used": coupon.used,
			"maximum_use": coupon.maximum_use,
			"valid_from": coupon.valid_from,
			"valid_upto": coupon.valid_upto,
			"campaign": campaign.get("campaign_name") if campaign else None,
			"campaign_id": campaign.get("name") if campaign else None,
		}

	voucher = frappe.db.get_value("CH Voucher", {"voucher_code": code},
		["name", "voucher_code", "voucher_type", "status", "original_amount",
		 "balance", "valid_from", "valid_upto", "company"], as_dict=True)
	if voucher:
		_require_document_read("CH Voucher", voucher.name)
		campaign = _get_linked_campaign("CH Voucher", voucher.name)
		company = _consistent_company(
			voucher.company,
			campaign.get("company") if campaign else None,
		)
		_require_lookup_scope(company)
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
			"campaign": campaign.get("campaign_name") if campaign else None,
			"campaign_id": campaign.get("name") if campaign else None,
		}

	return {"found": False}


def _get_linked_campaign(reference_doctype, reference_name):
	parent = frappe.db.get_value(
		"CH Campaign Code",
		{
			"parenttype": "CH Coupon Campaign",
			"parentfield": "codes",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		},
		"parent",
	)
	if not parent:
		return None
	_require_document_read("CH Coupon Campaign", parent)
	return frappe.db.get_value(
		"CH Coupon Campaign",
		parent,
		["name", "campaign_name", "company"],
		as_dict=True,
	)


def _require_document_read(doctype, name):
	if is_privileged_user():
		return
	if not frappe.has_permission(
		doctype,
		ptype="read",
		doc=name,
		user=frappe.session.user,
	):
		frappe.throw(
			_("You do not have read permission for {0} {1}.").format(doctype, name),
			frappe.PermissionError,
		)


def _consistent_company(*companies):
	companies = {company for company in companies if company}
	if len(companies) > 1:
		frappe.throw(_("The code has inconsistent company references."), frappe.PermissionError)
	return next(iter(companies), None)


def _require_lookup_scope(company, warehouse=None):
	if is_privileged_user():
		return
	if not company:
		frappe.throw(
			_("The code is not linked to an accessible company."),
			frappe.PermissionError,
		)

	get_company_scope(requested_company=company)
	try:
		from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
	except (ImportError, ModuleNotFoundError):
		frappe.throw(
			_("Location scope validation is unavailable. Contact an administrator."),
			frappe.PermissionError,
		)
	assert_user_has_store_scope(
		company=company,
		warehouse=warehouse,
		user=frappe.session.user,
		msg=_("You are not permitted to view this code."),
	)
