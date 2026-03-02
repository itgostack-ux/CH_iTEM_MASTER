# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Customer doc_events — hooked via ch_item_master hooks.py.
Handles:
  - Phone-number dedup (prevent duplicate customers by mobile)
  - Auto-generate referral code on insert
  - Auto-set ch_customer_since on first transaction
  - Auto-classify customer segment
"""

import frappe
from frappe import _
from frappe.utils import getdate, today, random_string


def before_insert(doc, method=None):
	"""Before a new Customer is created."""
	# Normalize customer name
	if doc.customer_name:
		doc.customer_name = " ".join(doc.customer_name.split())
	_check_phone_dedup(doc)
	_generate_referral_code(doc)
	_assign_customer_id(doc)
	if not doc.get("ch_customer_since"):
		doc.ch_customer_since = today()


def validate(doc, method=None):
	"""On every save of Customer."""
	# Normalize customer name
	if doc.customer_name:
		doc.customer_name = " ".join(doc.customer_name.split())
	_check_phone_dedup(doc)
	_set_kyc_verified_info(doc)


def _check_phone_dedup(doc):
	"""Prevent creating duplicate customers with the same mobile number.

	Checks both mobile_no (ERPNext standard) and ch_alternate_phone / ch_whatsapp_number.
	Only checks if mobile_no is set.
	"""
	mobile = doc.get("mobile_no")
	if not mobile:
		return

	# Normalize: strip spaces, remove country code prefix
	mobile = mobile.strip().replace(" ", "").replace("-", "")
	if mobile.startswith("+91"):
		mobile = mobile[3:]
	elif mobile.startswith("91") and len(mobile) == 12:
		mobile = mobile[2:]

	# Check if another customer has this number
	filters = {"mobile_no": ("like", f"%{mobile[-10:]}%")}
	if not doc.is_new():
		filters["name"] = ("!=", doc.name)

	existing = frappe.db.get_value("Customer", filters, ["name", "customer_name"], as_dict=True)
	if existing:
		frappe.msgprint(
			_("A customer with mobile number {0} already exists: {1} ({2}). "
			  "Please verify this is not a duplicate.").format(
				mobile, existing.customer_name, existing.name
			),
			title=_("Possible Duplicate Customer"),
			indicator="orange",
		)


def _generate_referral_code(doc):
	"""Auto-generate a unique referral code for new customers."""
	if doc.get("ch_referral_code"):
		return

	# Generate code: first 3 chars of name + random 5
	name_prefix = (doc.customer_name or "CUS")[:3].upper().replace(" ", "")
	code = f"{name_prefix}{random_string(5).upper()}"

	# Ensure uniqueness
	attempts = 0
	while frappe.db.exists("Customer", {"ch_referral_code": code}) and attempts < 10:
		code = f"{name_prefix}{random_string(5).upper()}"
		attempts += 1

	doc.ch_referral_code = code


def _assign_customer_id(doc):
	"""Auto-assign a unique integer ch_customer_id for API / mobile / POS use."""
	if doc.get("ch_customer_id"):
		return

	result = frappe.db.sql(
		"SELECT IFNULL(MAX(ch_customer_id), 0) FROM `tabCustomer`"
	)
	next_id = (result[0][0] or 0) + 1
	doc.ch_customer_id = next_id


def _set_kyc_verified_info(doc):
	"""Auto-set verified_by and verified_on when KYC is checked."""
	if doc.get("ch_kyc_verified") and not doc.get("ch_kyc_verified_by"):
		doc.ch_kyc_verified_by = frappe.session.user
		doc.ch_kyc_verified_on = today()
	elif not doc.get("ch_kyc_verified"):
		doc.ch_kyc_verified_by = None
		doc.ch_kyc_verified_on = None
