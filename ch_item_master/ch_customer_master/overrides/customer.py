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

import re

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
	_generate_membership_id(doc)
	if not doc.get("ch_customer_since"):
		doc.ch_customer_since = today()


def validate(doc, method=None):
	"""On every save of Customer."""
	# Normalize customer name
	if doc.customer_name:
		doc.customer_name = " ".join(doc.customer_name.split())
	_validate_phone_format(doc)
	_validate_id_documents(doc)
	_track_phone_change(doc)
	_check_phone_dedup(doc)
	_set_kyc_verified_info(doc)


def _validate_phone_format(doc):
	"""Validate all phone fields are valid 10-digit Indian numbers."""
	from buyback.utils import validate_indian_phone

	for field, label in (
		("mobile_no", "Mobile Number"),
		("ch_alternate_phone", "Alternate Phone"),
		("ch_whatsapp_number", "WhatsApp Number"),
	):
		val = doc.get(field)
		if val:
			doc.set(field, validate_indian_phone(val, label))


# PAN: 5 uppercase letters + 4 digits + 1 uppercase letter (e.g. ABCDE1234F)
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# Aadhaar: exactly 12 digits (no leading zero per UIDAI spec)
_AADHAAR_RE = re.compile(r"^[2-9]\d{11}$")


def _validate_id_documents(doc):
	"""Validate PAN and Aadhaar formats when provided."""

	# ── PAN validation (when ID type is PAN Card) ──
	id_type = doc.get("ch_id_type")
	id_number = (doc.get("ch_id_number") or "").strip()

	if id_type == "PAN Card" and id_number:
		id_number_upper = id_number.upper()
		if not _PAN_RE.match(id_number_upper):
			frappe.throw(
				_("Invalid PAN number '{0}'. PAN must be 10 characters in format: "
				  "ABCDE1234F (5 letters + 4 digits + 1 letter).").format(id_number),
				title=_("Invalid PAN"),
			)
		doc.ch_id_number = id_number_upper

	# ── Aadhaar validation ──
	aadhaar = (doc.get("ch_aadhaar_number") or "").strip().replace(" ", "")
	if aadhaar:
		if not _AADHAAR_RE.match(aadhaar):
			frappe.throw(
				_("Invalid Aadhaar number '{0}'. Aadhaar must be exactly 12 digits "
				  "and cannot start with 0 or 1.").format(doc.get("ch_aadhaar_number")),
				title=_("Invalid Aadhaar"),
			)
		doc.ch_aadhaar_number = aadhaar


def _check_phone_dedup(doc):
	"""Block creating duplicate customers with the same mobile number.

	Checks mobile_no against existing customers. Throws error (hard block)
	to prevent data fragmentation across duplicate records.
	"""
	mobile = doc.get("mobile_no")
	if not mobile:
		return

	from buyback.utils import normalize_indian_phone
	digits = normalize_indian_phone(mobile)
	if len(digits) < 10:
		return

	# Check if another customer has this number
	filters = {"mobile_no": ("like", f"%{digits[-10:]}%")}
	if not doc.is_new():
		filters["name"] = ("!=", doc.name)

	existing = frappe.db.get_value("Customer", filters, ["name", "customer_name"], as_dict=True)
	if existing:
		frappe.throw(
			_("A customer with mobile number {0} already exists: <b>{1}</b> ({2}). "
			  "Please update the existing customer record instead of creating a new one. "
			  "If the customer wants to change their phone number, use the existing record.").format(
				mobile, existing.customer_name, existing.name
			),
			title=_("Duplicate Phone Number"),
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

	frappe.db.sql("SELECT GET_LOCK('customer_id_gen', 10)")
	try:
		result = frappe.db.sql(
			"SELECT IFNULL(MAX(ch_customer_id), 0) FROM `tabCustomer`"
		)
		next_id = (result[0][0] or 0) + 1
		doc.ch_customer_id = next_id
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK('customer_id_gen')")


def _set_kyc_verified_info(doc):
	"""Auto-set verified_by and verified_on when KYC is checked."""
	if doc.get("ch_kyc_verified") and not doc.get("ch_kyc_verified_by"):
		doc.ch_kyc_verified_by = frappe.session.user
		doc.ch_kyc_verified_on = today()
	elif not doc.get("ch_kyc_verified"):
		doc.ch_kyc_verified_by = None
		doc.ch_kyc_verified_on = None


def _track_phone_change(doc):
	"""Log previous phone numbers when mobile_no changes.

	Appends the old number to ch_previous_phones (newline-separated)
	so there is a complete audit trail of phone number changes.
	"""
	if doc.is_new():
		return

	old_mobile = doc.db_get("mobile_no")
	new_mobile = doc.get("mobile_no")

	if not old_mobile or old_mobile == new_mobile:
		return

	# Append old number to previous phones log
	prev = (doc.get("ch_previous_phones") or "").strip()
	timestamp = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")
	entry = f"{old_mobile} (changed {timestamp})"
	if prev:
		doc.ch_previous_phones = f"{entry}\n{prev}"
	else:
		doc.ch_previous_phones = entry

	frappe.msgprint(
		_("Phone number changed from {0} to {1}. Previous number has been logged.").format(
			old_mobile, new_mobile
		),
		title=_("Phone Number Updated"),
		indicator="blue",
	)


def _generate_membership_id(doc):
	"""Auto-generate a customer-facing membership ID (e.g. GG-10045).

	Format: GG-<5-digit zero-padded sequential number>
	Uses the ch_customer_id as the base number.
	"""
	if doc.get("ch_membership_id"):
		return

	cust_id = doc.get("ch_customer_id")
	if cust_id:
		doc.ch_membership_id = f"GG-{int(cust_id):05d}"
	else:
		# Fallback: generate from max existing membership sequence
		frappe.db.sql("SELECT GET_LOCK('membership_id_gen', 10)")
		try:
			result = frappe.db.sql(
				"SELECT IFNULL(MAX(CAST(SUBSTRING(ch_membership_id, 4) AS UNSIGNED)), 0) "
				"FROM `tabCustomer` WHERE ch_membership_id IS NOT NULL AND ch_membership_id != ''"
			)
			next_id = (result[0][0] or 0) + 1
			doc.ch_membership_id = f"GG-{next_id:05d}"
		finally:
			frappe.db.sql("SELECT RELEASE_LOCK('membership_id_gen')")
