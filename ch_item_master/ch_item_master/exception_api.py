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
                    pos_profile=None, pos_invoice=None, customer=None):
	"""Create a new CH Exception Request in Pending state.

	Returns dict with request name + exception type config (for frontend
	to decide whether to show OTP dialog, call manager, etc.).
	"""
	etype = frappe.get_cached_doc("CH Exception Type", exception_type)
	if not etype.enabled:
		frappe.throw(_("Exception type {0} is disabled").format(exception_type))

	exc = frappe.new_doc("CH Exception Request")
	exc.exception_type = exception_type
	exc.company = company
	exc.requested_by = frappe.session.user
	exc.requested_reason = reason
	exc.requested_value = flt(requested_value)
	exc.original_value = flt(original_value)
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
                      resolution_value=None, remarks=None):
	"""Approve an exception request.

	If the exception type requires OTP, validates it first.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status != "Pending":
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status))
	if exc.docstatus == 1:
		frappe.throw(_("Exception {0} is already submitted").format(exception_name))

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
			frappe.throw(_("OTP verification failed: {0}").format(result.get("message")))
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
def reject_exception(exception_name, reason=None):
	"""Reject an exception request."""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status != "Pending":
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status))

	exc.reject(reason=reason)
	return {"name": exc.name, "status": "Rejected"}


@frappe.whitelist()
def check_exception_valid(exception_name):
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
def request_exception_otp(exception_name, mobile_no):
	"""Generate an OTP for an exception request approval.

	Uses the exception type as the OTP purpose.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status != "Pending":
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status))

	from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
	otp_code = CHOTPLog.generate_otp(
		mobile_no=mobile_no,
		purpose=exc.exception_type,
		reference_doctype="CH Exception Request",
		reference_name=exception_name,
	)
	return {"otp_sent": True, "mobile_no": mobile_no}


# ─────────────────────────────────────────────────────────────────────────────
# Query / Report helpers
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_pending_exceptions(company=None, store_warehouse=None, exception_type=None):
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
def get_exception_summary(company, from_date=None, to_date=None):
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
