# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Customer 360 API — A single API that returns everything about a customer.
Used by the frontend dashboard, mobile app, and internal tools.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today

from ch_item_master.security import get_company_scope


@frappe.whitelist()
def get_customer_360(customer, company=None):
	"""Return a comprehensive view of a customer.

	Args:
		customer: Customer name/id
		company: (optional) Filter transactions/devices/visits by company

	Returns:
		dict with keys: profile, kyc, payment_accounts, devices, loyalty,
		recent_transactions, store_visits, segment, referrals
	"""
	frappe.has_permission("Customer", "read", customer, throw=True)
	if not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer {0} does not exist").format(customer))

	company_scope = get_company_scope(requested_company=company)
	if not company and company_scope and len(company_scope) == 1:
		company = company_scope[0]

	cust = frappe.get_doc("Customer", customer)

	return {
		"profile": _get_profile(cust),
		"kyc": _get_kyc(cust),
		"payment_accounts": _get_payment_accounts(cust),
		"devices": _get_devices(customer, company=company),
		"loyalty": _get_loyalty(customer, company=company),
		"recent_transactions": _get_recent_transactions(customer, company=company),
		"store_visits": _get_store_visits(cust, company=company),
		"segment": _get_segment(cust),
		"referrals": _get_referrals(customer),
		"summary": _get_summary(cust),
		"sold_plans": _get_sold_plans(customer, company=company),
		"vouchers": _get_vouchers(customer),
		"coupon_usage": _get_coupon_usage(customer, company=company),
		"refunds": _get_refunds(customer, company=company),
		"claims_and_escalations": _get_claims_and_escalations(customer, company=company),
		"communications": _get_communications(customer),
		"feedback": _get_feedback(cust),
		"company_filter": company,
	}


def _get_profile(cust):
	"""Basic profile information."""
	return {
		"name": cust.name,
		"customer_name": cust.customer_name,
		"customer_type": cust.customer_type,
		"customer_group": cust.customer_group,
		"territory": cust.territory,
		"mobile_no": cust.mobile_no,
		"email_id": cust.email_id,
		"image": cust.image,
		"date_of_birth": cust.get("ch_date_of_birth"),
		"anniversary_date": cust.get("ch_anniversary_date"),
		"customer_since": cust.get("ch_customer_since"),
		"alternate_phone": cust.get("ch_alternate_phone"),
		"whatsapp_number": cust.get("ch_whatsapp_number"),
		"customer_image": cust.get("ch_customer_image"),
		"preferred_language": cust.get("ch_preferred_language"),
		"communication_preference": cust.get("ch_communication_preference"),
		"is_subscribed": cust.get("ch_is_subscribed"),
		"primary_address": cust.primary_address,
		"gstin": cust.get("gstin"),
		"pan": cust.get("pan"),
		"relationship_executive": cust.get("ch_relationship_executive"),
	}


def _get_kyc(cust):
	"""KYC verification status."""
	return {
		"customer_photo": cust.get("ch_customer_photo"),
		"id_type": cust.get("ch_id_type"),
		"id_number": cust.get("ch_id_number"),
		"aadhaar_number": _mask_aadhaar(cust.get("ch_aadhaar_number")),
		"aadhaar_document": cust.get("ch_aadhaar_document"),
		"id_front_image": cust.get("ch_id_front_image"),
		"id_back_image": cust.get("ch_id_back_image"),
		"kyc_verified": cust.get("ch_kyc_verified"),
		"kyc_verified_by": cust.get("ch_kyc_verified_by"),
		"kyc_verified_on": cust.get("ch_kyc_verified_on"),
		"kyc_source_order": cust.get("ch_kyc_source_order"),
		"total_kyc_verifications": cust.get("ch_total_kyc_verifications"),
		"device_photo_front": cust.get("ch_device_photo_front"),
		"device_photo_back": cust.get("ch_device_photo_back"),
		"device_photo_screen": cust.get("ch_device_photo_screen"),
		"device_photo_imei": cust.get("ch_device_photo_imei"),
	}


def _get_payment_accounts(cust):
	"""All saved payment methods."""
	accounts = []
	for row in cust.get("ch_payment_accounts", []):
		accounts.append({
			"account_label": row.account_label,
			"payment_mode": row.payment_mode,
			"bank_name": row.bank_name,
			"account_holder_name": row.account_holder_name,
			"account_no": _mask_account(row.account_no),
			"ifsc_code": row.ifsc_code,
			"upi_id": row.upi_id,
			"is_default": row.is_default,
			"is_verified": row.is_verified,
		})
	return accounts


def _get_devices(customer, company=None):
	"""All devices associated with this customer."""
	if not frappe.db.exists("DocType", "CH Customer Device"):
		return []

	filters = {"customer": customer}
	if company:
		filters["company"] = company

	devices = frappe.get_all(
		"CH Customer Device",
		filters=filters,
		fields=[
			"name", "serial_no", "item_code", "item_name", "brand",
			"imei_number", "current_status", "purchase_date",
			"warranty_status", "warranty_expiry", "warranty_plan_name",
			"buyback_date", "buyback_price", "buyback_grade",
		],
		order_by="purchase_date desc",
	)

	# Enrich with VAS plans and lifecycle logs
	for device in devices:
		device["vas_plans"] = frappe.get_all(
			"CH Customer Device VAS",
			filters={"parent": device.name},
			fields=["plan_name", "status", "valid_from", "valid_to", "claims_used", "max_claims"],
		)
		# Lifecycle history from CH Serial Lifecycle
		if device.get("serial_no") and frappe.db.exists("DocType", "CH Serial Lifecycle"):
			lifecycle_name = frappe.db.get_value(
				"CH Serial Lifecycle", {"serial_no": device.serial_no}, "name"
			)
			if lifecycle_name:
				device["lifecycle_logs"] = frappe.get_all(
					"CH Serial Lifecycle Log",
					filters={"parent": lifecycle_name},
					fields=["log_timestamp", "from_status", "to_status", "changed_by", "company", "warehouse", "remarks"],
					order_by="log_timestamp desc",
					limit=20,
				)
			else:
				device["lifecycle_logs"] = []
		else:
			device["lifecycle_logs"] = []

	return devices


def _get_loyalty(customer, company=None):
	"""Loyalty points summary and recent transactions."""
	if not frappe.db.exists("DocType", "CH Loyalty Transaction"):
		return {"balance": 0, "recent": []}

	company_cond = "AND company = %s" if company else ""
	company_args = [customer, company] if company else [customer]

	# Current balance
	result = frappe.db.sql(
		f"""SELECT IFNULL(SUM(points), 0) as balance
		FROM `tabCH Loyalty Transaction`
		WHERE customer = %s AND docstatus = 1 AND is_expired = 0 {company_cond}""",
		company_args,
		as_dict=True,
	)
	balance = cint(result[0].balance) if result else 0

	# Earned by company
	by_company = frappe.db.sql(
		"""SELECT company, SUM(points) as total
		FROM `tabCH Loyalty Transaction`
		WHERE customer = %s AND docstatus = 1 AND is_expired = 0
		GROUP BY company""",
		customer,
		as_dict=True,
	)

	# Recent transactions
	loyalty_filters = {"customer": customer, "docstatus": 1}
	if company:
		loyalty_filters["company"] = company

	recent = frappe.get_all(
		"CH Loyalty Transaction",
		filters=loyalty_filters,
		fields=[
			"name", "transaction_date", "company", "transaction_type",
			"points", "closing_balance", "remarks",
		],
		order_by="transaction_date desc",
		limit=20,
	)

	return {
		"balance": balance,
		"by_company": by_company,
		"recent": recent,
	}


def _get_recent_transactions(customer, company=None):
	"""Recent transactions across all apps, optionally filtered by company."""
	transactions = []

	# Sales Invoices
	si_filters = {"customer": customer, "docstatus": 1}
	if company:
		si_filters["company"] = company
	invoices = frappe.get_all(
		"Sales Invoice",
		filters=si_filters,
		fields=["name", "posting_date", "company", "grand_total", "status"],
		order_by="posting_date desc",
		limit=10,
	)
	for inv in invoices:
		inv["type"] = "Purchase"
		transactions.append(inv)

	# Service Requests
	if frappe.db.exists("DocType", "Service Request"):
		sr_filters = {"customer": customer}
		if company:
			sr_filters["company"] = company
		srs = frappe.get_all(
			"Service Request",
			filters=sr_filters,
			fields=["name", "creation", "company", "status", "device_item_name"],
			order_by="creation desc",
			limit=10,
		)
		for sr in srs:
			sr["type"] = "Service"
			sr["posting_date"] = sr.pop("creation")
			transactions.append(sr)

	# Buyback Requests — use customer Link field (with mobile fallback)
	if frappe.db.exists("DocType", "Buyback Request"):
		bb_filters = {"customer": customer}
		if company:
			bb_filters["company"] = company
		bbs = frappe.get_all(
			"Buyback Request",
			filters=bb_filters,
			fields=["name", "creation", "company", "item_full_name", "buyback_price", "deal_status", "status"],
			order_by="creation desc",
			limit=10,
		)
		for bb in bbs:
			bb["type"] = "Buyback"
			bb["posting_date"] = bb.pop("creation")
			transactions.append(bb)

	# Sort all by date
	transactions.sort(key=lambda x: str(x.get("posting_date", "")), reverse=True)
	return transactions[:20]


def _get_store_visits(cust, company=None):
	"""Store visit history, optionally filtered by company."""
	visits = []
	for row in cust.get("ch_stores_visited", []):
		if company and row.company and row.company != company:
			continue
		visits.append({
			"visit_date": row.visit_date,
			"store": row.store,
			"company": row.company,
			"visit_type": row.visit_type,
			"reference_doctype": row.reference_doctype,
			"reference_name": row.reference_name,
			"staff": row.staff,
			"remarks": row.remarks,
		})
	return visits


def _get_segment(cust):
	"""Customer segment and rating."""
	return {
		"segment": cust.get("ch_customer_segment"),
		"rating": cust.get("ch_customer_rating"),
		"referral_code": cust.get("ch_referral_code"),
		"referral_source": cust.get("ch_referral_source"),
	}


def _get_referrals(customer):
	"""Customers referred by this customer."""
	referral_code = frappe.db.get_value("Customer", customer, "ch_referral_code")
	if not referral_code:
		return {"code": None, "count": 0, "referred_customers": []}

	referred = frappe.get_all(
		"Customer",
		filters={"ch_referred_by": customer},
		fields=["name", "customer_name", "ch_customer_since", "mobile_no"],
		order_by="ch_customer_since desc",
	)

	return {
		"code": referral_code,
		"count": len(referred),
		"referred_customers": referred,
	}


def _get_summary(cust):
	"""Quick summary stats."""
	return {
		"total_purchases": flt(cust.get("ch_total_purchases")),
		"total_services": cint(cust.get("ch_total_services")),
		"total_buybacks": cint(cust.get("ch_total_buybacks")),
		"active_devices": cint(cust.get("ch_active_devices")),
		"loyalty_balance": cint(cust.get("ch_loyalty_points_balance")),
		"last_visit_date": cust.get("ch_last_visit_date"),
		"last_visit_store": cust.get("ch_last_visit_store"),
	}


def _get_sold_plans(customer, company=None):
	"""Warranty and AMC / care plans from CH Sold Plan."""
	if not frappe.db.exists("DocType", "CH Sold Plan"):
		return []

	filters = {"customer": customer, "docstatus": 1}
	if company:
		filters["company"] = company

	return frappe.get_all(
		"CH Sold Plan",
		filters=filters,
		fields=[
			"name", "plan_name", "plan_type", "item_code", "item_name",
			"serial_no", "start_date", "end_date", "status",
			"sales_invoice", "company",
		],
		order_by="end_date desc",
		limit=30,
	)


def _get_vouchers(customer):
	"""Vouchers issued to this customer."""
	if not frappe.db.exists("DocType", "CH Voucher"):
		return []

	return frappe.get_all(
		"CH Voucher",
		filters={"issued_to": customer, "docstatus": 1},
		fields=[
			"name", "voucher_code", "voucher_type", "original_amount",
			"balance", "status", "valid_from", "valid_upto",
			"source_type", "issued_date",
		],
		order_by="issued_date desc",
		limit=20,
	)


def _get_coupon_usage(customer, company=None):
	"""Invoices where this customer used a coupon code."""
	company_cond = "AND pi.company = %(company)s" if company else ""
	params = {"customer": customer}
	if company:
		params["company"] = company

	return frappe.db.sql(
		f"""SELECT pi.name, pi.posting_date, pi.custom_coupon_code AS coupon_code, pi.grand_total, pi.status
		FROM `tabSales Invoice` pi
		WHERE pi.customer = %(customer)s AND pi.docstatus = 1
		  AND pi.custom_coupon_code IS NOT NULL AND pi.custom_coupon_code != ''
		  {company_cond}
		ORDER BY pi.posting_date DESC LIMIT 20""",
		params,
		as_dict=True,
	)


def _get_refunds(customer, company=None):
	"""Return/refund invoices."""
	company_cond = "AND company = %(company)s" if company else ""
	params = {"customer": customer}
	if company:
		params["company"] = company

	# POS returns
	returns = frappe.db.sql(
		f"""SELECT name, posting_date, grand_total, return_against, status, 'POS Invoice' as doctype
		FROM `tabPOS Invoice`
		WHERE customer = %(customer)s AND docstatus = 1 AND is_return = 1
		  {company_cond}
		ORDER BY posting_date DESC LIMIT 20""",
		params,
		as_dict=True,
	)

	# Sales Invoice returns (non-POS)
	si_returns = frappe.db.sql(
		f"""SELECT name, posting_date, grand_total, return_against, status, 'Sales Invoice' as doctype
		FROM `tabSales Invoice`
		WHERE customer = %(customer)s AND docstatus = 1 AND is_return = 1
		  {company_cond}
		ORDER BY posting_date DESC LIMIT 10""",
		params,
		as_dict=True,
	)

	all_returns = returns + si_returns
	all_returns.sort(key=lambda x: str(x.get("posting_date", "")), reverse=True)
	return all_returns[:20]


def _get_claims_and_escalations(customer, company=None):
	"""Warranty claims and exception requests for this customer."""
	result = {"warranty_claims": [], "exception_requests": []}

	if frappe.db.exists("DocType", "CH Warranty Claim"):
		wc_filters = {"customer": customer}
		if company:
			wc_filters["company"] = company
		result["warranty_claims"] = frappe.get_all(
			"CH Warranty Claim",
			filters=wc_filters,
			fields=[
				"name", "claim_date", "item_name", "brand", "serial_no",
				"coverage_type", "claim_status", "issue_category",
				"repair_status", "company",
			],
			order_by="claim_date desc",
			limit=20,
		)

	if frappe.db.exists("DocType", "CH Exception Request"):
		er_filters = {"customer": customer}
		result["exception_requests"] = frappe.get_all(
			"CH Exception Request",
			filters=er_filters,
			fields=[
				"name", "creation", "exception_type", "status",
				"reference_doctype", "reference_name", "requested_by",
			],
			order_by="creation desc",
			limit=20,
		)

	return result


def _get_communications(customer):
	"""Recent communications (emails, SMS, WhatsApp) linked to this customer."""
	return frappe.db.sql(
		"""SELECT c.name, c.communication_date, c.communication_type,
		       c.subject, c.sender, c.recipients, c.status
		FROM `tabCommunication` c
		JOIN `tabCommunication Link` cl ON cl.parent = c.name
		WHERE cl.link_doctype = 'Customer' AND cl.link_name = %(customer)s
		ORDER BY c.communication_date DESC LIMIT 20""",
		{"customer": customer},
		as_dict=True,
	)


def _get_feedback(cust):
	"""Customer feedback entries."""
	feedback = []
	for row in cust.get("ch_feedback", []):
		feedback.append({
			"feedback_date": row.feedback_date,
			"feedback_type": row.feedback_type,
			"rating": row.rating,
			"comments": row.comments,
			"reference_doctype": row.reference_doctype,
			"reference_name": row.reference_name,
			"collected_by": row.collected_by,
		})
	return feedback


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mask_aadhaar(aadhaar):
	"""Mask Aadhaar number: show only last 4 digits."""
	if not aadhaar:
		return None
	aadhaar = aadhaar.replace(" ", "")
	if len(aadhaar) >= 8:
		return "XXXX XXXX " + aadhaar[-4:]
	return aadhaar


def _mask_account(account_no):
	"""Mask bank account: show only last 4 digits."""
	if not account_no:
		return None
	if len(account_no) >= 4:
		return "X" * (len(account_no) - 4) + account_no[-4:]
	return account_no


@frappe.whitelist()
def merge_customers(primary_customer, duplicate_customer):
	"""Merge a duplicate customer into the primary customer.

	Transfers all transactions, devices, loyalty, and visits.
	"""
	frappe.only_for("System Manager")

	if primary_customer == duplicate_customer:
		frappe.throw(_("Cannot merge a customer with itself"))

	primary = frappe.get_doc("Customer", primary_customer)
	duplicate = frappe.get_doc("Customer", duplicate_customer)

	# Transfer CH Customer Devices
	if frappe.db.exists("DocType", "CH Customer Device"):
		frappe.db.sql(
			"""UPDATE `tabCH Customer Device`
			SET customer = %s, customer_name = %s
			WHERE customer = %s""",
			(primary_customer, primary.customer_name, duplicate_customer),
		)

	# Transfer CH Loyalty Transactions
	if frappe.db.exists("DocType", "CH Loyalty Transaction"):
		frappe.db.sql(
			"""UPDATE `tabCH Loyalty Transaction`
			SET customer = %s, customer_name = %s
			WHERE customer = %s""",
			(primary_customer, primary.customer_name, duplicate_customer),
		)

	# Transfer Buyback Requests (re-link customer field)
	if frappe.db.exists("DocType", "Buyback Request"):
		frappe.db.sql(
			"""UPDATE `tabBuyback Request`
			SET customer = %s
			WHERE customer = %s""",
			(primary_customer, duplicate_customer),
		)
		# Also re-link any buybacks matched by duplicate's mobile
		dup_mobile = (duplicate.mobile_no or "").strip().replace(" ", "").replace("-", "")
		if dup_mobile:
			dup_mobile10 = dup_mobile[-10:]
			frappe.db.sql(
				"""UPDATE `tabBuyback Request`
				SET customer = %s
				WHERE (customer IS NULL OR customer = '')
				AND mobile_no = %s""",
				(primary_customer, dup_mobile10),
			)

	# Copy over missing profile data
	profile_fields = [
		"ch_date_of_birth", "ch_anniversary_date", "ch_alternate_phone",
		"ch_whatsapp_number", "ch_aadhaar_number", "ch_aadhaar_document",
	]
	for field in profile_fields:
		if not primary.get(field) and duplicate.get(field):
			primary.set(field, duplicate.get(field))

	# Copy payment accounts from duplicate
	for pa in duplicate.get("ch_payment_accounts", []):
		primary.append("ch_payment_accounts", {
			"account_label": pa.account_label,
			"payment_mode": pa.payment_mode,
			"bank_name": pa.bank_name,
			"branch": pa.branch,
			"account_holder_name": pa.account_holder_name,
			"account_no": pa.account_no,
			"ifsc_code": pa.ifsc_code,
			"upi_id": pa.upi_id,
			"is_default": 0,  # Don't carry default flag
			"is_verified": pa.is_verified,
		})

	# Copy store visits from duplicate
	for visit in duplicate.get("ch_stores_visited", []):
		primary.append("ch_stores_visited", {
			"visit_date": visit.visit_date,
			"store": visit.store,
			"company": visit.company,
			"visit_type": visit.visit_type,
			"reference_doctype": visit.reference_doctype,
			"reference_name": visit.reference_name,
			"staff": visit.staff,
			"remarks": f"[Merged from {duplicate_customer}] {visit.remarks or ''}",
		})

	primary.save(ignore_permissions=True)

	# Use ERPNext's built-in rename to handle all linked documents
	frappe.rename_doc("Customer", duplicate_customer, primary_customer, merge=True)

	# Recalculate summary
	from ch_item_master.ch_customer_master.hooks import _update_activity_summary
	_update_activity_summary(primary_customer)

	frappe.msgprint(
		_("Customer {0} merged into {1} successfully.").format(
			duplicate.customer_name, primary.customer_name
		),
		indicator="green",
		alert=True,
	)

	return primary_customer
