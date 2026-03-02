# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Customer 360 API — A single API that returns everything about a customer.
Used by the frontend dashboard, mobile app, and internal tools.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today


@frappe.whitelist()
def get_customer_360(customer):
	"""Return a comprehensive view of a customer.

	Args:
		customer: Customer name/id

	Returns:
		dict with keys: profile, kyc, payment_accounts, devices, loyalty,
		recent_transactions, store_visits, segment, referrals
	"""
	if not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer {0} does not exist").format(customer))

	cust = frappe.get_doc("Customer", customer)

	return {
		"profile": _get_profile(cust),
		"kyc": _get_kyc(cust),
		"payment_accounts": _get_payment_accounts(cust),
		"devices": _get_devices(customer),
		"loyalty": _get_loyalty(customer),
		"recent_transactions": _get_recent_transactions(customer),
		"store_visits": _get_store_visits(cust),
		"segment": _get_segment(cust),
		"referrals": _get_referrals(customer),
		"summary": _get_summary(cust),
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
	}


def _get_kyc(cust):
	"""KYC verification status."""
	return {
		"aadhaar_number": _mask_aadhaar(cust.get("ch_aadhaar_number")),
		"aadhaar_document": cust.get("ch_aadhaar_document"),
		"kyc_verified": cust.get("ch_kyc_verified"),
		"kyc_verified_by": cust.get("ch_kyc_verified_by"),
		"kyc_verified_on": cust.get("ch_kyc_verified_on"),
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


def _get_devices(customer):
	"""All devices associated with this customer."""
	if not frappe.db.exists("DocType", "CH Customer Device"):
		return []

	devices = frappe.get_all(
		"CH Customer Device",
		filters={"customer": customer},
		fields=[
			"name", "serial_no", "item_code", "item_name", "brand",
			"imei_number", "current_status", "purchase_date",
			"warranty_status", "warranty_expiry", "warranty_plan_name",
			"buyback_date", "buyback_price", "buyback_grade",
		],
		order_by="purchase_date desc",
	)

	# Enrich with VAS plans
	for device in devices:
		device["vas_plans"] = frappe.get_all(
			"CH Customer Device VAS",
			filters={"parent": device.name},
			fields=["plan_name", "status", "valid_from", "valid_to", "claims_used", "max_claims"],
		)

	return devices


def _get_loyalty(customer):
	"""Loyalty points summary and recent transactions."""
	if not frappe.db.exists("DocType", "CH Loyalty Transaction"):
		return {"balance": 0, "recent": []}

	# Current balance
	result = frappe.db.sql(
		"""SELECT IFNULL(SUM(points), 0) as balance
		FROM `tabCH Loyalty Transaction`
		WHERE customer = %s AND docstatus = 1 AND is_expired = 0""",
		customer,
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
	recent = frappe.get_all(
		"CH Loyalty Transaction",
		filters={"customer": customer, "docstatus": 1},
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


def _get_recent_transactions(customer):
	"""Recent transactions across all apps."""
	transactions = []

	# Sales Invoices
	invoices = frappe.get_all(
		"Sales Invoice",
		filters={"customer": customer, "docstatus": 1},
		fields=["name", "posting_date", "company", "grand_total", "status"],
		order_by="posting_date desc",
		limit=10,
	)
	for inv in invoices:
		inv["type"] = "Purchase"
		transactions.append(inv)

	# Service Requests
	if frappe.db.exists("DocType", "Service Request"):
		srs = frappe.get_all(
			"Service Request",
			filters={"customer": customer},
			fields=["name", "creation", "company", "status", "device_item_name"],
			order_by="creation desc",
			limit=10,
		)
		for sr in srs:
			sr["type"] = "Service"
			sr["posting_date"] = sr.pop("creation")
			transactions.append(sr)

	# Buyback Requests (match by mobile)
	mobile = frappe.db.get_value("Customer", customer, "mobile_no")
	if mobile and frappe.db.exists("DocType", "Buyback Request"):
		mobile_last10 = mobile.strip().replace(" ", "")[-10:]
		bbs = frappe.get_all(
			"Buyback Request",
			filters={"mobile_no": ("like", f"%{mobile_last10}%")},
			fields=["name", "creation", "item_full_name", "buyback_price", "deal_status", "status"],
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


def _get_store_visits(cust):
	"""Store visit history."""
	visits = []
	for row in cust.get("ch_stores_visited", []):
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
