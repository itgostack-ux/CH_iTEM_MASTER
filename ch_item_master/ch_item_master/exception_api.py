# Copyright (c) 2026, GoStack and contributors
# Exception Approval Framework — centralised APIs for raising, approving,
# validating, and querying exception requests across all apps.

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, getdate, add_to_date


# ─────────────────────────────────────────────────────────────────────────────
# Core APIs — whitelisted for POS / frontend consumption
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def raise_exception(exception_type, company, reason, requested_value=0,
                    original_value=0, reference_doctype=None, reference_name=None,
                    item_code=None, serial_no=None, store_warehouse=None,
                    pos_profile=None, pos_invoice=None, customer=None) -> dict:
	"""Create a new CH Exception Request in Pending state.

	Returns dict with request name + exception type config (for frontend
	to decide whether to show OTP dialog, call manager, etc.).
	"""
	etype = frappe.get_cached_doc("CH Exception Type", exception_type)
	if not etype.enabled:
		frappe.throw(_("Exception type {0} is disabled").format(exception_type), title=_("API Error"))

	frappe.has_permission("CH Exception Request", "create", throw=True)

	# IM-12 fix: Prevent duplicate open exception requests for same item+store+type
	if item_code and store_warehouse:
		existing = frappe.db.exists("CH Exception Request", {
			"exception_type": exception_type,
			"item_code": item_code,
			"store_warehouse": store_warehouse,
			"status": ("in", ["Pending", "Awaiting Approval"]),
		})
		if existing:
			frappe.throw(
				_("An open exception request ({0}) already exists for item {1} at this store. "
				  "Please wait for it to be resolved.").format(existing, item_code),
				title=_("Duplicate Exception Request"),
			)

	exc = frappe.new_doc("CH Exception Request")
	exc.exception_type = exception_type
	exc.company = company
	exc.requested_by = frappe.session.user
	exc.requested_reason = reason

	# IM-7 fix: Validate requested_value bounds — no negative or absurdly large values
	req_val = flt(requested_value)
	orig_val = flt(original_value)
	if req_val < 0:
		frappe.throw(_("Requested value cannot be negative"), title=_("Invalid Value"))
	if orig_val < 0:
		frappe.throw(_("Original value cannot be negative"), title=_("Invalid Value"))
	max_allowed = flt(etype.get("max_exception_value")) or 10_000_000
	if req_val > max_allowed:
		frappe.throw(
			_("Requested value {0} exceeds the maximum allowed limit of {1}").format(
				req_val, max_allowed),
			title=_("Value Too High"),
		)

	exc.requested_value = req_val
	exc.original_value = orig_val
	exc.reference_doctype = reference_doctype
	exc.reference_name = reference_name
	exc.item_code = item_code
	exc.serial_no = serial_no
	exc.store_warehouse = store_warehouse
	exc.pos_profile = pos_profile
	exc.pos_invoice = pos_invoice
	exc.customer = customer

	# Auto-approve if value is within threshold
	if (flt(etype.max_value_without_approval) > 0
			and flt(requested_value) <= flt(etype.max_value_without_approval)):
		exc.status = "Auto-Approved"
		exc.approval_channel = "Auto-Policy"
		exc.approved_at = now_datetime()
		exc.resolved_at = now_datetime()
		exc.resolved_by = frappe.session.user
		exc.approval_expiry = add_to_date(
			now_datetime(), minutes=etype.validity_minutes or 30
		)
		exc.insert(ignore_permissions=True)
		exc.submit()
		return {
			"name": exc.name,
			"status": "Auto-Approved",
			"approval_expiry": str(exc.approval_expiry),
		}

	exc.insert(ignore_permissions=True)

	return {
		"name": exc.name,
		"status": "Pending",
		"requires_otp": bool(etype.requires_otp),
		"requires_ho_approval": bool(etype.requires_ho_approval),
		"approval_level": etype.approval_level,
	}


@frappe.whitelist()
def approve_exception(exception_name, approver_user=None, channel=None,
                      otp_code=None, otp_mobile=None,
                      resolution_value=None, remarks=None) -> dict:
	"""Approve an exception request.

	If the exception type requires OTP, validates it first.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status != "Pending":
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status), title=_("API Error"))

	frappe.has_permission("CH Exception Request", "write", throw=True)

	if exc.docstatus == 1:
		frappe.throw(_("Exception {0} is already submitted").format(exception_name), title=_("API Error"))

	etype = frappe.get_cached_doc("CH Exception Type", exc.exception_type)
	approver_user = approver_user or frappe.session.user

	# OTP validation
	otp_ref = None
	if etype.requires_otp and otp_code and otp_mobile:
		from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
		result = CHOTPLog.verify_otp(
			mobile_no=otp_mobile,
			purpose=exc.exception_type,
			otp_code=otp_code,
			reference_doctype="CH Exception Request",
			reference_name=exception_name,
		)
		if not result.get("valid"):
			frappe.throw(_("OTP verification failed: {0}").format(result.get("message")), title=_("API Error"))
		otp_ref = result.get("otp_log")

	exc.approve(
		approver=approver_user,
		channel=channel or ("OTP" if otp_ref else "Manager PIN"),
		otp_reference=otp_ref,
		resolution_value=resolution_value,
		remarks=remarks,
	)

	return {
		"name": exc.name,
		"status": "Approved",
		"approval_expiry": str(exc.approval_expiry),
	}


@frappe.whitelist()
def reject_exception(exception_name, reason=None) -> dict:
	"""Reject an exception request."""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status != "Pending":
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status), title=_("API Error"))

	frappe.has_permission("CH Exception Request", "write", throw=True)

	exc.reject(reason=reason)
	return {"name": exc.name, "status": "Rejected"}


@frappe.whitelist()
def check_exception_valid(exception_name) -> dict:
	"""Check if an approved exception is still within its validity window.

	Called by POS / other apps before consuming the exception.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	valid = exc.is_valid()
	return {
		"name": exc.name,
		"valid": valid,
		"status": exc.status,
		"approval_expiry": str(exc.approval_expiry) if exc.approval_expiry else None,
	}


@frappe.whitelist()
def request_exception_otp(exception_name, mobile_no) -> dict:
	"""Generate an OTP for an exception request approval.

	Uses the exception type as the OTP purpose.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status != "Pending":
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status), title=_("API Error"))

	from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
	otp_code = CHOTPLog.generate_otp(
		mobile_no=mobile_no,
		purpose=exc.exception_type,
		reference_doctype="CH Exception Request",
		reference_name=exception_name,
	)
	# Send OTP via email alongside any SMS/WhatsApp
	try:
		from buyback.buyback.whatsapp_notifications import send_otp_email, _get_email_for_mobile
		approver_email = _get_email_for_mobile(mobile_no)
		send_otp_email(approver_email, otp_code, exc.exception_type, exception_name)
	except Exception:
		frappe.log_error(title="Exception OTP email delivery failed")
	return {"otp_sent": True, "mobile_no": mobile_no}


# ─────────────────────────────────────────────────────────────────────────────
# Query / Report helpers
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_pending_exceptions(company=None, store_warehouse=None, exception_type=None) -> list:
	"""Return all pending exception requests, optionally filtered."""
	filters = {"status": "Pending", "docstatus": 0}
	if company:
		filters["company"] = company
	if store_warehouse:
		filters["store_warehouse"] = store_warehouse
	if exception_type:
		filters["exception_type"] = exception_type

	return frappe.get_all("CH Exception Request",
		filters=filters,
		fields=[
			"name", "exception_type", "company", "store_warehouse",
			"requested_by", "requested_by_name", "requested_reason",
			"requested_value", "original_value", "reference_doctype",
			"reference_name", "item_code", "serial_no", "raised_at",
		],
		order_by="raised_at desc",
		limit_page_length=50,
	)


@frappe.whitelist()
def get_exception_summary(company, from_date=None, to_date=None) -> dict:
	"""Return aggregated exception stats for reporting / dashboard."""
	if not from_date:
		from_date = getdate()
	if not to_date:
		to_date = getdate()

	data = frappe.db.sql("""
		SELECT
			exception_type,
			status,
			COUNT(*) as count,
			SUM(requested_value) as total_requested,
			SUM(CASE WHEN status='Approved' OR status='Auto-Approved'
			    THEN COALESCE(resolution_value, requested_value) ELSE 0 END
			) as total_approved_value
		FROM `tabCH Exception Request`
		WHERE company = %s
		  AND DATE(raised_at) BETWEEN %s AND %s
		  AND docstatus != 2
		GROUP BY exception_type, status
		ORDER BY exception_type, status
	""", (company, str(from_date), str(to_date)), as_dict=True)

	return data


# ─────────────────────────────────────────────────────────────────────────────
# Scheduled: expire stale pending requests
# ─────────────────────────────────────────────────────────────────────────────

def expire_stale_exceptions():
	"""Mark old pending exceptions as Expired.

	Called via scheduler (hourly). An exception that has been pending for
	more than 24 hours is auto-expired.
	"""
	cutoff = add_to_date(now_datetime(), hours=-24)
	stale = frappe.get_all("CH Exception Request",
		filters={
			"status": "Pending",
			"docstatus": 0,
			"raised_at": ("<=", cutoff),
		},
		pluck="name",
		limit=200,
	)

	for name in stale:
		try:
			exc = frappe.get_doc("CH Exception Request", name)
			exc.status = "Expired"
			exc.resolved_at = now_datetime()
			exc.resolution_remarks = "Auto-expired after 24 hours"
			exc.save(ignore_permissions=True)
			exc.submit()
		except Exception:
			frappe.log_error(f"Failed to expire exception {name}")

	if stale:
		frappe.db.commit()

@frappe.whitelist()
def get_item_original_value(item_code: str, company: str | None = None) -> float:
        """Return the standard selling price for an item (for Exception Request auto-fill)."""
        frappe.has_permission("CH Exception Request", "create", throw=True)

        # 1. Try CH Item Price (POS channel, Active)
        price = frappe.db.get_value(
                "CH Item Price",
                {"item_code": item_code, "channel": "POS", "status": "Active"},
                "selling_price",
        )
        if price and flt(price) > 0:
                return flt(price)

        # 2. Fallback: ERPNext Item Price (Standard Selling price list)
        filters = {"item_code": item_code, "selling": 1}
        if company:
                price_list = frappe.db.get_value(
                        "Company", company, "default_selling_price_list"
                ) or "Standard Selling"
                filters["price_list"] = price_list

        price = frappe.db.get_value("Item Price", filters, "price_list_rate")
        return flt(price) if price else 0.0