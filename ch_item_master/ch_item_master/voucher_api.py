# Copyright (c) 2026, GoStack and contributors
# Voucher / Gift Card / Store Credit API
#
# Central API for voucher lifecycle: issue, validate, redeem, refund, expire.
# Called from POS UI, external website/app via whitelisted endpoints.

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, now_datetime, cint

from ch_item_master.config import (
	get_int_setting,
	get_list_setting,
	is_privileged_user,
	require_role_setting,
)
from ch_item_master.security import (
	get_company_filter_value,
	require_scoped_document_action,
)


_VOUCHER_ISSUE_ROLES = ("CH Master Manager", "CH Price Manager", "Sales Manager")
_VOUCHER_REDEMPTION_ROLES = ("Sales User", "Sales Manager", "CH Price Manager")
_VOUCHER_REFUND_ROLES = ("Sales Manager", "CH Price Manager")
_VOUCHER_TOPUP_ROLES = ("Sales Manager", "CH Price Manager")
_VOUCHER_VIEW_ROLES = ("Sales User", "Sales Manager", "CH Price Manager", "CH Viewer")
_VOUCHER_SOURCE_DOCTYPES = (
	"Sales Invoice",
	"POS Invoice",
	"Delivery Note",
	"Payment Entry",
	"CH Coupon Campaign",
	"Active VAS Plans",
)
_VOUCHER_REFERENCE_DOCTYPES = ("Sales Invoice", "POS Invoice", "Delivery Note", "Payment Entry")


def _allowed_doctypes(fieldname, defaults):
	return get_list_setting(fieldname, defaults)


def _voucher_document(voucher_code):
	voucher_name = frappe.db.get_value("CH Voucher", {"voucher_code": voucher_code}, "name")
	if not voucher_name:
		frappe.throw(_("Voucher not found"), title=_("API Error"))
	return frappe.get_doc("CH Voucher", voucher_name)


def _authorize_voucher(voucher, role_field, default_roles, action, permission_type="write", lock=True):
	require_scoped_document_action(
		voucher,
		role_field,
		default_roles,
		action=action,
		permission_types=(permission_type,),
		lock=lock,
	)


def _validate_reference(doctype, name, company, allowed_doctypes, require_submitted=False):
	if not doctype or not name:
		frappe.throw(_("Reference DocType and document are both required."))
	if doctype not in allowed_doctypes:
		frappe.throw(_("{0} is not an allowed voucher reference type.").format(doctype))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("Referenced {0} {1} does not exist.").format(doctype, name))
	if not is_privileged_user() and not frappe.has_permission(
		doctype, "read", doc=name, user=frappe.session.user
	):
		frappe.throw(_("You cannot access the referenced document."), frappe.PermissionError)

	meta = frappe.get_meta(doctype)
	fields = ["docstatus"]
	for fieldname in ("company", "customer", "pos_profile", "set_warehouse"):
		if meta.has_field(fieldname):
			fields.append(fieldname)
	values = frappe.db.get_value(doctype, name, fields, as_dict=True) or frappe._dict()
	if require_submitted and cint(values.get("docstatus")) != 1:
		frappe.throw(_("Referenced {0} must be submitted.").format(doctype))
	if "company" in fields and values.get("company") != company:
		frappe.throw(_("Voucher and referenced document must belong to the same company."))

	warehouse = values.get("set_warehouse")
	if values.get("pos_profile"):
		profile = frappe.db.get_value(
			"POS Profile", values.pos_profile, ["company", "warehouse"], as_dict=True
		) or frappe._dict()
		if profile.get("company") and profile.company != company:
			frappe.throw(_("The referenced POS Profile belongs to another company."))
		warehouse = profile.get("warehouse") or warehouse
	if warehouse and not is_privileged_user():
		try:
			from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
		except (ImportError, ModuleNotFoundError):
			frappe.throw(_("Location scope validation is unavailable."), frappe.PermissionError)
		assert_user_has_store_scope(company=company, warehouse=warehouse)
	return values


def _resolve_source_doctype(source_document, allowed_doctypes):
	if not source_document:
		return None
	matches = [doctype for doctype in allowed_doctypes if frappe.db.exists(doctype, source_document)]
	if len(matches) != 1:
		frappe.throw(_("Specify an unambiguous Source DocType for the source document."))
	return matches[0]

# ─────────────────────────────────────────────────────────────────────────────
# Issue / Create
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def issue_voucher(voucher_type, amount, company, customer=None, phone=None,
                  valid_days=365, source_type=None, source_document=None,
                  reason=None, single_use=0, min_order_amount=0,
                  max_discount_amount=0, applicable_channel=None,
                  applicable_item_group=None, sold_plan=None, source_doctype=None) -> dict:
	"""Issue a new voucher (Gift Card / Store Credit / Promo Voucher / Return Credit / VAS Voucher).

	Args:
		voucher_type: Gift Card | Store Credit | Promo Voucher | Return Credit | VAS Voucher
		amount: Face value
		company: Company
		customer: Optional customer link
		phone: Optional phone for SMS delivery
		valid_days: Days from today (default 365)
		source_type: Manual | Return | Promotion | Compensation | Purchase
		source_document: Link to source doc (Sales Invoice, etc.)
		reason: Free text note
		applicable_item_group: Item Group restriction for redemption
		sold_plan: Active VAS Plans that triggered this voucher (for VAS)

	Returns:
		dict with voucher_code, name, balance
	"""
	from datetime import timedelta

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Voucher amount must be greater than zero"), title=_("API Error"))
	maximum_amount = get_int_setting("voucher_max_amount", 500000, minimum=1)
	if amount > maximum_amount:
		frappe.throw(
			_("Voucher amount cannot exceed {0}").format(
				frappe.format_value(maximum_amount, {"fieldtype": "Currency"})
			),
			title=_("API Error"),
		)
	if flt(min_order_amount) < 0:
		frappe.throw(_("Minimum order amount cannot be negative"), title=_("API Error"))
	if flt(max_discount_amount) < 0:
		frappe.throw(_("Maximum discount amount cannot be negative"), title=_("API Error"))
	if cint(valid_days) < 1 or cint(valid_days) > 3650:
		frappe.throw(_("Validity must be between 1 and 3650 days"), title=_("API Error"))
	if voucher_type not in {"Gift Card", "Store Credit", "Promo Voucher", "Return Credit", "VAS Voucher"}:
		frappe.throw(_("Unsupported voucher type."), title=_("API Error"))
	source_type = source_type or "Manual"
	if source_type not in {"Manual", "Return", "Promotion", "Compensation", "Purchase"}:
		frappe.throw(_("Unsupported voucher source type."), title=_("API Error"))
	if source_type == "Return" and not source_document:
		frappe.throw(_("A return source document is required."), title=_("API Error"))

	allowed_sources = _allowed_doctypes("voucher_source_doctypes", _VOUCHER_SOURCE_DOCTYPES)
	if source_document:
		source_doctype = source_doctype or _resolve_source_doctype(source_document, allowed_sources)
		source_values = _validate_reference(
			source_doctype, source_document, company, allowed_sources, require_submitted=True
		)
		if customer and source_values.get("customer") and source_values.customer != customer:
			frappe.throw(_("Voucher customer does not match the source document customer."))
	elif source_doctype:
		frappe.throw(_("A source document is required when Source DocType is supplied."))

	if customer:
		if not frappe.db.exists("Customer", customer):
			frappe.throw(_("Customer does not exist."))
		if not is_privileged_user() and not frappe.has_permission(
			"Customer", "read", doc=customer, user=frappe.session.user
		):
			frappe.throw(_("You cannot access this customer."), frappe.PermissionError)
	if sold_plan:
		plan_values = _validate_reference(
			"Active VAS Plans",
			sold_plan,
			company,
			frozenset({"Active VAS Plans"}),
		)
		if customer and plan_values.get("customer") and plan_values.customer != customer:
			frappe.throw(_("Voucher customer does not match the sold plan customer."))

	today = getdate(nowdate())
	valid_upto = today + timedelta(days=cint(valid_days) or 365)

	voucher = frappe.get_doc({
		"doctype": "CH Voucher",
		"voucher_type": voucher_type,
		"company": company,
		"original_amount": amount,
		"issued_to": customer,
		"phone": phone,
		"valid_from": str(today),
		"valid_upto": str(valid_upto),
		"source_type": source_type,
		"source_doctype": source_doctype,
		"source_document": source_document,
		"reason": reason,
		"single_use": cint(single_use),
		"min_order_amount": flt(min_order_amount),
		"max_discount_amount": flt(max_discount_amount),
		"applicable_channel": applicable_channel,
		"applicable_item_group": applicable_item_group,
		"sold_plan": sold_plan,
	})
	require_scoped_document_action(
		voucher,
		"voucher_issue_roles",
		_VOUCHER_ISSUE_ROLES,
		action=_("issue a voucher"),
		permission_types=("create", "submit"),
	)
	voucher.insert()

	# Add issue transaction
	voucher.append("transactions", {
		"transaction_type": "Issue",
		"amount": amount,
		"balance_after": amount,
		"transaction_date": now_datetime(),
		"note": f"Voucher issued: {voucher_type}",
	})
	voucher.save()

	# Submit to activate
	voucher.submit()

	return {
		"name": voucher.name,
		"voucher_code": voucher.voucher_code,
		"voucher_type": voucher.voucher_type,
		"balance": voucher.balance,
		"valid_upto": str(voucher.valid_upto),
	}


# ─────────────────────────────────────────────────────────────────────────────
# Validate (check if redeemable)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def validate_voucher(voucher_code, cart_total=0, customer=None, channel=None) -> dict:
	"""Validate a voucher code and return applicable discount.

	Args:
		voucher_code: The unique code entered by user
		cart_total: Current cart total (for min_order check)
		customer: Optional customer (for customer-bound vouchers)
		channel: Optional CH Price Channel (for channel-restricted vouchers)

	Returns:
		dict: {valid, voucher_name, voucher_type, balance, applicable_amount, reason}
	"""
	if not voucher_code:
		return {"valid": False, "reason": "No voucher code provided"}

	try:
		voucher = _voucher_document(voucher_code)
	except frappe.ValidationError:
		return {"valid": False, "reason": "Voucher code not found"}
	_authorize_voucher(
		voucher,
		"voucher_redemption_roles",
		_VOUCHER_REDEMPTION_ROLES,
		_("validate a voucher"),
		permission_type="read",
		lock=False,
	)

	today = getdate(nowdate())

	# Status check
	if voucher.status not in ("Active", "Partially Used"):
		return {"valid": False, "reason": f"Voucher is {voucher.status}"}

	# Must be submitted
	if cint(voucher.docstatus) != 1:
		return {"valid": False, "reason": "Voucher has not been activated (not submitted)"}

	# Date check
	if voucher.valid_from and today < getdate(voucher.valid_from):
		return {"valid": False, "reason": "Voucher is not yet active"}
	if voucher.valid_upto and today > getdate(voucher.valid_upto):
		return {"valid": False, "reason": "Voucher has expired"}

	# Balance check
	balance = flt(voucher.balance)
	if balance <= 0:
		return {"valid": False, "reason": "Voucher has no remaining balance"}

	# Customer check (if voucher is customer-bound)
	if voucher.issued_to:
		if not customer:
			return {"valid": False, "reason": "Customer is required for this voucher"}
		if voucher.issued_to != customer:
			return {"valid": False, "reason": "Voucher is issued to a different customer"}

	# Channel check
	if voucher.applicable_channel and voucher.applicable_channel != channel:
		return {"valid": False, "reason": f"Voucher is only valid for {voucher.applicable_channel} channel"}

	# Min order check
	cart_total = flt(cart_total)
	if flt(voucher.min_order_amount) > 0 and cart_total < flt(voucher.min_order_amount):
		return {
			"valid": False,
			"reason": f"Minimum order of ₹{voucher.min_order_amount:,.0f} required",
		}

	# Calculate applicable amount
	applicable = balance
	if voucher.single_use:
		applicable = balance  # Must use full balance
	if flt(voucher.max_discount_amount) > 0:
		applicable = min(applicable, flt(voucher.max_discount_amount))
	if cart_total > 0:
		applicable = min(applicable, cart_total)  # Can't exceed cart total

	return {
		"valid": True,
		"voucher_name": voucher.name,
		"voucher_code": voucher_code,
		"voucher_type": voucher.voucher_type,
		"balance": balance,
		"applicable_amount": applicable,
		"single_use": voucher.single_use,
	}


# ─────────────────────────────────────────────────────────────────────────────
# Redeem (debit balance)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def redeem_voucher(voucher_code, amount, pos_invoice=None, reference_doctype=None,
                   reference_document=None) -> dict:
	"""Redeem (use) a voucher — deducts from balance.

	Args:
		voucher_code: The unique voucher code
		amount: Amount to deduct
		pos_invoice: POS Invoice name (if redeemed at POS)
		reference_doctype/reference_document: Optional reference

	Returns:
		dict: {success, redeemed_amount, remaining_balance, voucher_name}
	"""
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Redemption amount must be greater than zero"), title=_("API Error"))

	voucher = _voucher_document(voucher_code)
	_authorize_voucher(
		voucher,
		"voucher_redemption_roles",
		_VOUCHER_REDEMPTION_ROLES,
		_("redeem a voucher"),
		lock=True,
	)

	if voucher.docstatus != 1:
		frappe.throw(_("Voucher has not been activated (not submitted)"), title=_("API Error"))

	if voucher.status not in ("Active", "Partially Used"):
		frappe.throw(
			_("Voucher is {0} and cannot be redeemed").format(voucher.status),
			title=_("API Error"),
		)

	if voucher.valid_upto and getdate(voucher.valid_upto) < getdate(nowdate()):
		frappe.throw(
			_("Voucher expired on {0}").format(frappe.format(voucher.valid_upto, "Date")),
			title=_("API Error"),
		)

	balance = flt(voucher.balance)
	if balance <= 0:
		frappe.throw(_("Voucher has no remaining balance"), title=_("API Error"))

	reference_required = bool(get_int_setting("voucher_redemption_reference_required", 1))
	if pos_invoice:
		if reference_document and reference_document != pos_invoice:
			frappe.throw(_("POS invoice and reference document do not match."))
		if reference_doctype and reference_doctype != "Sales Invoice":
			frappe.throw(_("POS invoice references must use Sales Invoice."))
		reference_doctype = "Sales Invoice"
		reference_document = pos_invoice
	elif reference_doctype or reference_document:
		if not (reference_doctype and reference_document):
			frappe.throw(_("Reference DocType and document are both required."))
	elif reference_required and not is_privileged_user():
		frappe.throw(_("A submitted transaction reference is required to redeem a voucher."))

	reference_values = frappe._dict()
	if reference_document:
		reference_values = _validate_reference(
			reference_doctype,
			reference_document,
			voucher.company,
			_allowed_doctypes("voucher_reference_doctypes", _VOUCHER_REFERENCE_DOCTYPES),
			require_submitted=True,
		)
		if voucher.issued_to and reference_values.get("customer") != voucher.issued_to:
			frappe.throw(_("Voucher and transaction customers do not match."))
		if any(
			t.transaction_type == "Redeem"
			and t.reference_doctype == reference_doctype
			and t.reference_document == reference_document
			for t in (voucher.transactions or [])
		):
			frappe.throw(_("This transaction has already redeemed the voucher."))

	# Enforce item group restriction (e.g. VAS vouchers → Accessories only)
	if voucher.applicable_item_group and pos_invoice:
		_validate_voucher_item_groups(pos_invoice, voucher.applicable_item_group)

	# Single-use: forfeit entire balance on first redemption
	if voucher.single_use:
		existing_redeems = [t for t in (voucher.transactions or [])
		                    if t.transaction_type == "Redeem"]
		if existing_redeems:
			frappe.throw(_("This voucher has already been redeemed (single-use)"), title=_("API Error"))
		redeem_amount = min(amount, balance)
		new_balance = 0
	else:
		redeem_amount = min(amount, balance)
		new_balance = balance - redeem_amount

	# Add transaction
	voucher.append("transactions", {
		"transaction_type": "Redeem",
		"amount": -redeem_amount,
		"balance_after": new_balance,
		"transaction_date": now_datetime(),
		"pos_invoice": pos_invoice,
		"reference_doctype": reference_doctype,
		"reference_document": reference_document,
		"note": f"Redeemed ₹{redeem_amount:,.2f} at {pos_invoice or 'counter'}",
	})

	voucher.balance = new_balance
	voucher.flags.ignore_validate_update_after_submit = True
	voucher.save()

	# Explicitly persist status for submitted docs (validate changes aren't tracked)
	voucher._auto_set_status()
	voucher.db_set("status", voucher.status, update_modified=False)

	# Post GL entry to reduce gift card liability (if account configured)
	_post_voucher_gl(
		voucher_name=voucher.name,
		company=voucher.company,
		amount=redeem_amount,
		transaction_type="Redeem",
		posting_date=frappe.utils.nowdate(),
		reference_doc=pos_invoice,
	)

	# Audit
	try:
		from ch_pos.audit import log_business_event
		log_business_event(
			event_type="Voucher Redemption",
			ref_doctype="CH Voucher", ref_name=voucher.name,
			before=f"Balance ₹{balance}",
			after=f"Balance ₹{new_balance}",
			remarks=f"Redeemed ₹{redeem_amount} at {pos_invoice or 'counter'}",
			company=voucher.company,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Audit log failed for voucher redeem {voucher.name}")

	return {
		"success": True,
		"redeemed_amount": redeem_amount,
		"remaining_balance": new_balance,
		"voucher_name": voucher.name,
		"voucher_code": voucher.voucher_code,
	}


# ─────────────────────────────────────────────────────────────────────────────
# Refund (credit back on cancellation)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def refund_voucher(voucher_code, amount, pos_invoice=None, reason=None) -> dict:
	"""Refund amount back to a voucher (e.g. on invoice cancellation).

	Args:
		voucher_code: The unique voucher code
		amount: Amount to credit back
		pos_invoice: The cancelled POS Invoice
		reason: Reason for refund

	Returns:
		dict: {success, refunded_amount, new_balance}
	"""
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Refund amount must be greater than zero"), title=_("API Error"))

	voucher = _voucher_document(voucher_code)
	_authorize_voucher(
		voucher,
		"voucher_refund_roles",
		_VOUCHER_REFUND_ROLES,
		_("refund a voucher balance"),
		lock=True,
	)

	if voucher.voucher_type == "VAS Voucher":
		frappe.throw(_("VAS Vouchers cannot be refunded"), title=_("API Error"))
	if not pos_invoice and not is_privileged_user():
		frappe.throw(_("The original Sales Invoice is required for a voucher refund."))
	if pos_invoice:
		invoice = _validate_reference(
			"Sales Invoice",
			pos_invoice,
			voucher.company,
			frozenset({"Sales Invoice"}),
		)
		if cint(invoice.get("docstatus")) not in (1, 2):
			frappe.throw(_("The referenced Sales Invoice must be submitted or cancelled."))
		if voucher.issued_to and invoice.get("customer") != voucher.issued_to:
			frappe.throw(_("Voucher and refund customers do not match."))
		redeemed = sum(
			-flt(transaction.amount)
			for transaction in (voucher.transactions or [])
			if transaction.transaction_type == "Redeem" and transaction.pos_invoice == pos_invoice
		)
		already_refunded = sum(
			flt(transaction.amount)
			for transaction in (voucher.transactions or [])
			if transaction.transaction_type == "Refund" and transaction.pos_invoice == pos_invoice
		)
		if amount > redeemed - already_refunded:
			frappe.throw(_("Refund exceeds the voucher amount redeemed on this invoice."))

	new_balance = flt(voucher.balance) + amount
	# Don't exceed original amount
	if new_balance > flt(voucher.original_amount):
		new_balance = flt(voucher.original_amount)
		amount = new_balance - flt(voucher.balance)

	voucher.append("transactions", {
		"transaction_type": "Refund",
		"amount": amount,
		"balance_after": new_balance,
		"transaction_date": now_datetime(),
		"pos_invoice": pos_invoice,
		"reference_doctype": "Sales Invoice" if pos_invoice else None,
		"reference_document": pos_invoice,
		"note": reason or f"Refund from {pos_invoice}",
	})

	voucher.balance = new_balance
	voucher.flags.ignore_validate_update_after_submit = True
	voucher.save()

	# Explicitly persist status for submitted docs
	voucher._auto_set_status()
	voucher.db_set("status", voucher.status, update_modified=False)

	return {
		"success": True,
		"refunded_amount": amount,
		"new_balance": new_balance,
	}


# ─────────────────────────────────────────────────────────────────────────────
# Top-Up
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def topup_voucher(voucher_code, amount, reason=None, reference_doctype=None,
                  reference_document=None) -> dict:
	"""Add balance to an existing voucher (Gift Card top-up).

	Returns:
		dict: {success, new_balance}
	"""
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Top-up amount must be greater than zero"), title=_("API Error"))

	voucher = _voucher_document(voucher_code)
	_authorize_voucher(
		voucher,
		"voucher_topup_roles",
		_VOUCHER_TOPUP_ROLES,
		_("top up a voucher"),
		lock=True,
	)

	if voucher.voucher_type == "VAS Voucher":
		frappe.throw(_("VAS Vouchers cannot be topped up"), title=_("API Error"))

	if voucher.status in ("Cancelled", "Expired"):
		frappe.throw(_("Cannot top-up a {0} voucher").format(voucher.status), title=_("API Error"))
	reference_values = frappe._dict()
	if reference_doctype or reference_document:
		reference_values = _validate_reference(
			reference_doctype,
			reference_document,
			voucher.company,
			_allowed_doctypes("voucher_reference_doctypes", _VOUCHER_REFERENCE_DOCTYPES),
			require_submitted=True,
		)
		if voucher.issued_to and reference_values.get("customer") and reference_values.customer != voucher.issued_to:
			frappe.throw(_("Voucher and top-up transaction customers do not match."))
		if any(
			transaction.transaction_type == "Top-Up"
			and transaction.reference_doctype == reference_doctype
			and transaction.reference_document == reference_document
			for transaction in (voucher.transactions or [])
		):
			frappe.throw(_("This transaction has already topped up the voucher."))
	elif get_int_setting("voucher_topup_reference_required", 1) and not is_privileged_user():
		frappe.throw(_("A submitted payment or transaction reference is required for a top-up."))

	new_balance = flt(voucher.balance) + amount
	new_original = flt(voucher.original_amount) + amount
	maximum_amount = get_int_setting("voucher_max_amount", 500000, minimum=1)
	if new_original > maximum_amount:
		frappe.throw(
			_("Voucher value cannot exceed {0}.").format(
				frappe.format_value(maximum_amount, {"fieldtype": "Currency"})
			)
		)

	voucher.append("transactions", {
		"transaction_type": "Top-Up",
		"amount": amount,
		"balance_after": new_balance,
		"transaction_date": now_datetime(),
		"reference_doctype": reference_doctype,
		"reference_document": reference_document,
		"note": reason or f"Top-up ₹{amount:,.2f}",
	})

	voucher.balance = new_balance
	voucher.original_amount = new_original
	voucher.flags.ignore_validate_update_after_submit = True
	voucher.save()

	# Explicitly persist status for submitted docs
	voucher._auto_set_status()
	voucher.db_set("status", voucher.status, update_modified=False)

	return {"success": True, "new_balance": new_balance}


# ─────────────────────────────────────────────────────────────────────────────
# Balance Check
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def check_balance(voucher_code) -> dict:
	"""Check voucher balance (can be called by customer via website/app).

	Returns:
		dict: {voucher_type, status, balance, original_amount, valid_upto}
	"""
	if not voucher_code:
		return {"error": "Voucher code required"}

	try:
		voucher = _voucher_document(voucher_code)
	except frappe.ValidationError:
		return {"error": "Voucher not found"}
	_authorize_voucher(
		voucher,
		"voucher_view_roles",
		_VOUCHER_VIEW_ROLES,
		_("view a voucher balance"),
		permission_type="read",
		lock=False,
	)

	return {
		"voucher_type": voucher.voucher_type,
		"status": voucher.status,
		"balance": flt(voucher.balance),
		"original_amount": flt(voucher.original_amount),
		"valid_upto": str(voucher.valid_upto) if voucher.valid_upto else None,
	}


# ─────────────────────────────────────────────────────────────────────────────
# Customer Voucher List
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_customer_vouchers(customer, company=None, include_expired=False) -> list:
	"""Get all vouchers for a customer.

	Args:
		customer: Customer name
		company: Optional company filter
		include_expired: Include expired/used vouchers

	Returns:
		list of voucher dicts
	"""
	require_role_setting("voucher_view_roles", _VOUCHER_VIEW_ROLES, action=_("view customer vouchers"))
	frappe.has_permission("CH Voucher", "read", throw=True)
	if not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer does not exist."))
	frappe.has_permission("Customer", "read", doc=customer, throw=True)
	filters = {"issued_to": customer}
	company_filter = get_company_filter_value(requested_company=company)
	if company_filter is not None:
		filters["company"] = company_filter
	if not include_expired:
		filters["status"] = ("in", ["Active", "Partially Used"])
	limit = min(get_int_setting("voucher_list_limit", 200, minimum=1), 1000)

	return frappe.get_all(
		"CH Voucher",
		filters=filters,
		fields=[
			"name", "voucher_code", "voucher_type", "status",
			"original_amount", "balance", "valid_from", "valid_upto",
		],
		order_by="creation desc",
		limit_page_length=limit,
	)


# ─────────────────────────────────────────────────────────────────────────────
# Issue Return Credit (called from POS on returns)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def issue_return_credit(customer, amount, company, pos_invoice=None, reason=None) -> dict:
	"""Issue a Return Credit voucher when processing a return at POS.

	This replaces cash refund — customer gets store credit instead.

	Returns:
		dict: same as issue_voucher
	"""
	return issue_voucher(
		voucher_type="Return Credit",
		amount=amount,
		company=company,
		customer=customer,
		source_type="Return",
		source_doctype="Sales Invoice" if pos_invoice else None,
		source_document=pos_invoice,
		reason=reason or f"Return credit from {pos_invoice}",
		valid_days=365,
	)


# ─────────────────────────────────────────────────────────────────────────────
# Expiry Scheduler (called daily)
# ─────────────────────────────────────────────────────────────────────────────

def expire_vouchers():
	"""Expire one bounded batch with set-based balance and ledger updates."""
	today_date = getdate(nowdate())
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 5000)
	rows = frappe.db.sql(
		"""
			SELECT voucher.`name`, voucher.`balance`, COALESCE(MAX(txn.`idx`), 0) AS `last_idx`
			FROM `tabCH Voucher` voucher
			LEFT JOIN `tabCH Voucher Transaction` txn
			  ON txn.`parent` = voucher.`name`
			 AND txn.`parenttype` = 'CH Voucher'
			 AND txn.`parentfield` = 'transactions'
			WHERE voucher.`status` IN ('Active', 'Partially Used')
			  AND voucher.`valid_upto` < %(today)s
			GROUP BY voucher.`name`, voucher.`balance`, voucher.`valid_upto`
			ORDER BY voucher.`valid_upto` ASC, voucher.`name` ASC
			LIMIT %(fetch_limit)s
		""",
		{"today": today_date, "fetch_limit": batch_limit + 1},
		as_dict=True,
	)
	vouchers = rows[:batch_limit]
	if not vouchers:
		return {"expired": 0, "has_more": False}

	now = now_datetime()
	actor = frappe.session.user
	transaction_values = []
	for voucher in vouchers:
		remaining = flt(voucher.balance)
		if remaining <= 0:
			continue
		transaction_values.append((
			frappe.generate_hash(length=10),
			now,
			now,
			actor,
			actor,
			0,
			cint(voucher.last_idx) + 1,
			voucher.name,
			"CH Voucher",
			"transactions",
			"Expiry",
			-remaining,
			0,
			now,
			f"Voucher expired with ₹{remaining:,.2f} unused",
		))
	if transaction_values:
		frappe.db.bulk_insert(
			"CH Voucher Transaction",
			fields=[
				"name", "creation", "modified", "owner", "modified_by", "docstatus", "idx",
				"parent", "parenttype", "parentfield", "transaction_type", "amount",
				"balance_after", "transaction_date", "note",
			],
			values=transaction_values,
		)

	names = tuple(voucher.name for voucher in vouchers)
	frappe.db.sql(
		"""
			UPDATE `tabCH Voucher`
			SET `balance` = 0,
			    `status` = 'Expired',
			    `modified` = %(modified)s,
			    `modified_by` = %(actor)s
			WHERE `name` IN %(names)s
			  AND `status` IN ('Active', 'Partially Used')
			  AND `valid_upto` < %(today)s
		""",
		{"names": names, "today": today_date, "modified": now, "actor": actor},
	)
	frappe.logger("ch_item_master").info(
		f"Voucher expiry: {len(vouchers)} voucher(s) expired"
	)
	return {"expired": len(vouchers), "has_more": len(rows) > batch_limit}



# ─────────────────────────────────────────────────────────────────────────────
# Gift Card GL Entry helper
# ─────────────────────────────────────────────────────────────────────────────


def _post_voucher_gl(voucher_name, company, amount, transaction_type,
                     posting_date, reference_doc=None):
	"""Post a Journal Entry to record gift card liability movement.

	Requires `custom_gift_card_account` to be configured on the Company.
	If not configured, logs a warning and skips silently.

	Transaction types:
	  Issue  – Credit gift card liability (obligation created when card sold)
	  Redeem – Debit gift card liability (obligation fulfilled on redemption)
	  Expiry – Debit gift card liability (forfeited, moved to other income)
	"""
	gift_card_account = frappe.db.get_value(
		"Company", company, "custom_gift_card_account"
	)
	if not gift_card_account:
		frappe.logger("ch_item_master").warning(
			f"Voucher GL skipped for {voucher_name}: "
			f"custom_gift_card_account not configured on Company {company}"
		)
		return

	default_income_account = frappe.db.get_value(
		"Company", company, "default_income_account"
	)
	if not default_income_account:
		frappe.logger("ch_item_master").warning(
			f"Voucher GL skipped for {voucher_name}: default_income_account not set"
		)
		return

	# Build balanced Journal Entry
	if transaction_type == "Issue":
		# Cash already recorded by sale — credit liability to recognise obligation
		debit_account = default_income_account   # deferred revenue / contra
		credit_account = gift_card_account
		remarks = f"Gift card issued: {voucher_name}"
	elif transaction_type == "Redeem":
		# Obligation fulfilled — debit (extinguish) liability
		debit_account = gift_card_account
		credit_account = default_income_account  # Now earned
		remarks = f"Gift card redeemed: {voucher_name} at {reference_doc or 'counter'}"
	elif transaction_type == "Expiry":
		# Forfeited balance — transfer to income
		debit_account = gift_card_account
		credit_account = default_income_account
		remarks = f"Gift card expired: {voucher_name}"
	else:
		return

	try:
		je = frappe.new_doc("Journal Entry")
		je.company = company
		je.posting_date = posting_date
		je.voucher_type = "Journal Entry"
		je.user_remark = remarks
		je.cheque_no = voucher_name
		je.cheque_date = posting_date
		je.append("accounts", {
			"account": debit_account,
			"debit_in_account_currency": flt(amount),
			"credit_in_account_currency": 0,
			"reference_type": "CH Voucher",
			"reference_name": voucher_name,
		})
		je.append("accounts", {
			"account": credit_account,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": flt(amount),
			"reference_type": "CH Voucher",
			"reference_name": voucher_name,
		})
		je.flags.ignore_permissions = True
		je.flags.ch_system_generated_je = True
		je.save()
		je.submit()
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voucher GL Entry failed for {voucher_name}",
		)


def _validate_voucher_item_groups(pos_invoice, allowed_group):
	"""Validate that all items in the POS Invoice belong to the allowed item group.

	Throws if any line item falls outside the allowed group hierarchy.
	"""
	items = frappe.db.get_all(
		"Sales Invoice Item",
		filters={"parent": pos_invoice},
		fields=["item_code", "item_group"],
	)
	if not items:
		return

	# Build set of allowed groups (include sub-groups of allowed_group)
	allowed_groups = set()
	allowed_groups.add(allowed_group)
	sub_groups = frappe.get_all(
		"Item Group",
		filters={"lft": [">=", 0]},
		fields=["name", "lft", "rgt"],
	)
	parent_lft_rgt = frappe.db.get_value("Item Group", allowed_group, ["lft", "rgt"])
	if parent_lft_rgt:
		plft, prgt = parent_lft_rgt
		for sg in sub_groups:
			if sg.lft >= plft and sg.rgt <= prgt:
				allowed_groups.add(sg.name)

	violating = [
		i.item_code for i in items
		if i.item_group and i.item_group not in allowed_groups
	]

	if violating:
		frappe.throw(
			_("Voucher is restricted to {0}. These items are not eligible: {1}").format(
				frappe.bold(allowed_group),
				", ".join(frappe.bold(v) for v in violating[:5]),
			),
			title=_("Voucher Restriction"),
		)
