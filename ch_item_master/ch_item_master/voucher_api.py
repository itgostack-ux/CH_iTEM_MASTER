# Copyright (c) 2026, GoStack and contributors
# Voucher / Gift Card / Store Credit API
#
# Central API for voucher lifecycle: issue, validate, redeem, refund, expire.
# Called from POS UI, external website/app via whitelisted endpoints.

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, now_datetime, cint


# ─────────────────────────────────────────────────────────────────────────────
# Issue / Create
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def issue_voucher(voucher_type, amount, company, customer=None, phone=None,
                  valid_days=365, source_type=None, source_document=None,
                  reason=None, single_use=0, min_order_amount=0,
                  max_discount_amount=0, applicable_channel=None):
	"""Issue a new voucher (Gift Card / Store Credit / Promo Voucher / Return Credit).

	Args:
		voucher_type: Gift Card | Store Credit | Promo Voucher | Return Credit
		amount: Face value
		company: Company
		customer: Optional customer link
		phone: Optional phone for SMS delivery
		valid_days: Days from today (default 365)
		source_type: Manual | Return | Promotion | Compensation | Purchase
		source_document: Link to source doc (Sales Invoice, etc.)
		reason: Free text note

	Returns:
		dict with voucher_code, name, balance
	"""
	from datetime import timedelta

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Voucher amount must be greater than zero"))

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
		"source_type": source_type or "Manual",
		"reason": reason,
		"single_use": cint(single_use),
		"min_order_amount": flt(min_order_amount),
		"max_discount_amount": flt(max_discount_amount),
		"applicable_channel": applicable_channel,
	})
	voucher.insert(ignore_permissions=True)

	# Add issue transaction
	voucher.append("transactions", {
		"transaction_type": "Issue",
		"amount": amount,
		"balance_after": amount,
		"transaction_date": now_datetime(),
		"note": f"Voucher issued: {voucher_type}",
	})

	# Activate immediately
	voucher.status = "Active"
	voucher.save(ignore_permissions=True)

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
def validate_voucher(voucher_code, cart_total=0, customer=None, channel=None):
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

	voucher = frappe.db.get_value(
		"CH Voucher",
		{"voucher_code": voucher_code},
		["name", "voucher_type", "status", "balance", "original_amount",
		 "valid_from", "valid_upto", "issued_to", "company",
		 "min_order_amount", "max_discount_amount", "single_use",
		 "applicable_channel", "applicable_item_group"],
		as_dict=True,
	)

	if not voucher:
		return {"valid": False, "reason": "Voucher code not found"}

	today = getdate(nowdate())

	# Status check
	if voucher.status not in ("Active", "Partially Used"):
		return {"valid": False, "reason": f"Voucher is {voucher.status}"}

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
	if voucher.issued_to and customer and voucher.issued_to != customer:
		return {"valid": False, "reason": "Voucher is issued to a different customer"}

	# Channel check
	if voucher.applicable_channel and channel and voucher.applicable_channel != channel:
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

@frappe.whitelist()
def redeem_voucher(voucher_code, amount, pos_invoice=None, reference_doctype=None,
                   reference_document=None):
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
		frappe.throw(_("Redemption amount must be greater than zero"))

	voucher = frappe.get_doc("CH Voucher", {"voucher_code": voucher_code})
	if not voucher:
		frappe.throw(_("Voucher not found"))

	if voucher.status not in ("Active", "Partially Used"):
		frappe.throw(_("Voucher is {0} and cannot be redeemed").format(voucher.status))

	balance = flt(voucher.balance)
	if balance <= 0:
		frappe.throw(_("Voucher has no remaining balance"))

	# Cap at available balance
	redeem_amount = min(amount, balance)
	new_balance = balance - redeem_amount

	# Add transaction
	voucher.append("transactions", {
		"transaction_type": "Redeem",
		"amount": -redeem_amount,
		"balance_after": new_balance,
		"transaction_date": now_datetime(),
		"pos_invoice": pos_invoice,
		"reference_doctype": reference_doctype or ("POS Invoice" if pos_invoice else None),
		"reference_document": reference_document or pos_invoice,
		"note": f"Redeemed ₹{redeem_amount:,.2f} at {pos_invoice or 'counter'}",
	})

	voucher.balance = new_balance
	voucher.save(ignore_permissions=True)

	# Post GL entry to reduce gift card liability (if account configured)
	_post_voucher_gl(
		voucher_name=voucher.name,
		company=voucher.company,
		amount=redeem_amount,
		transaction_type="Redeem",
		posting_date=frappe.utils.nowdate(),
		reference_doc=pos_invoice,
	)

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

@frappe.whitelist()
def refund_voucher(voucher_code, amount, pos_invoice=None, reason=None):
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
		frappe.throw(_("Refund amount must be greater than zero"))

	voucher = frappe.get_doc("CH Voucher", {"voucher_code": voucher_code})
	if not voucher:
		frappe.throw(_("Voucher not found"))

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
		"reference_doctype": "POS Invoice" if pos_invoice else None,
		"reference_document": pos_invoice,
		"note": reason or f"Refund from {pos_invoice}",
	})

	voucher.balance = new_balance
	voucher.save(ignore_permissions=True)

	return {
		"success": True,
		"refunded_amount": amount,
		"new_balance": new_balance,
	}


# ─────────────────────────────────────────────────────────────────────────────
# Top-Up
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def topup_voucher(voucher_code, amount, reason=None):
	"""Add balance to an existing voucher (Gift Card top-up).

	Returns:
		dict: {success, new_balance}
	"""
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Top-up amount must be greater than zero"))

	voucher = frappe.get_doc("CH Voucher", {"voucher_code": voucher_code})
	if not voucher:
		frappe.throw(_("Voucher not found"))

	if voucher.status in ("Cancelled", "Expired"):
		frappe.throw(_("Cannot top-up a {0} voucher").format(voucher.status))

	new_balance = flt(voucher.balance) + amount
	new_original = flt(voucher.original_amount) + amount

	voucher.append("transactions", {
		"transaction_type": "Top-Up",
		"amount": amount,
		"balance_after": new_balance,
		"transaction_date": now_datetime(),
		"note": reason or f"Top-up ₹{amount:,.2f}",
	})

	voucher.balance = new_balance
	voucher.original_amount = new_original
	voucher.save(ignore_permissions=True)

	return {"success": True, "new_balance": new_balance}


# ─────────────────────────────────────────────────────────────────────────────
# Balance Check
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def check_balance(voucher_code):
	"""Check voucher balance (can be called by customer via website/app).

	Returns:
		dict: {voucher_type, status, balance, original_amount, valid_upto}
	"""
	if not voucher_code:
		return {"error": "Voucher code required"}

	voucher = frappe.db.get_value(
		"CH Voucher",
		{"voucher_code": voucher_code},
		["voucher_type", "status", "balance", "original_amount", "valid_upto"],
		as_dict=True,
	)

	if not voucher:
		return {"error": "Voucher not found"}

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
def get_customer_vouchers(customer, company=None, include_expired=False):
	"""Get all vouchers for a customer.

	Args:
		customer: Customer name
		company: Optional company filter
		include_expired: Include expired/used vouchers

	Returns:
		list of voucher dicts
	"""
	filters = {"issued_to": customer}
	if company:
		filters["company"] = company
	if not include_expired:
		filters["status"] = ("in", ["Active", "Partially Used"])

	return frappe.get_all(
		"CH Voucher",
		filters=filters,
		fields=[
			"name", "voucher_code", "voucher_type", "status",
			"original_amount", "balance", "valid_from", "valid_upto",
		],
		order_by="creation desc",
	)


# ─────────────────────────────────────────────────────────────────────────────
# Issue Return Credit (called from POS on returns)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def issue_return_credit(customer, amount, company, pos_invoice=None, reason=None):
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
		source_document=pos_invoice,
		reason=reason or f"Return credit from {pos_invoice}",
		valid_days=365,
	)


# ─────────────────────────────────────────────────────────────────────────────
# Expiry Scheduler (called daily)
# ─────────────────────────────────────────────────────────────────────────────

def expire_vouchers():
	"""Scheduled task: expire vouchers past valid_upto date."""
	today = getdate(nowdate())

	expired = frappe.get_all(
		"CH Voucher",
		filters={
			"status": ("in", ["Active", "Partially Used"]),
			"valid_upto": ("<", str(today)),
		},
		fields=["name", "balance", "voucher_code"],
	)

	for v in expired:
		voucher = frappe.get_doc("CH Voucher", v.name)
		remaining = flt(voucher.balance)
		if remaining > 0:
			voucher.append("transactions", {
				"transaction_type": "Expiry",
				"amount": -remaining,
				"balance_after": 0,
				"transaction_date": now_datetime(),
				"note": f"Voucher expired with ₹{remaining:,.2f} unused",
			})
			voucher.balance = 0
		voucher.status = "Expired"
		voucher.save(ignore_permissions=True)

	if expired:
		frappe.db.commit()
		frappe.logger("ch_item_master").info(
			f"Voucher expiry: {len(expired)} voucher(s) expired"
		)



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
		je.save()
		je.submit()
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Voucher GL Entry failed for {voucher_name}",
		)
