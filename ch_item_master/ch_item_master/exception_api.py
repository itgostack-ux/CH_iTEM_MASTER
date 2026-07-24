# Copyright (c) 2026, GoStack and contributors
# Exception Approval Framework — centralised APIs for raising, approving,
# validating, and querying exception requests across all apps.

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, getdate, now_datetime, nowdate

from ch_item_master.config import (
	get_enabled_role_users,
	get_int_setting,
	is_privileged_user,
	require_role_setting,
)
from ch_item_master.security import ensure_company_access, get_company_scope


_EXCEPTION_SUMMARY_ROLES = ("Store Manager", "Sales Manager", "Service Manager")
_MAX_EXCEPTION_SUMMARY_DAYS = 366


def _approver_mobile(user=None) -> str:
	"""Resolve the authenticated approver's trusted mobile number."""
	user = user or frappe.session.user
	row = frappe.db.get_value("User", user, ["mobile_no", "phone"], as_dict=True) or {}
	mobile = (row.get("mobile_no") or row.get("phone") or "").strip()
	if not mobile and frappe.db.exists("DocType", "Employee"):
		fields = [f for f in ("cell_number", "personal_mobile") if frappe.get_meta("Employee").has_field(f)]
		if fields:
			employee = frappe.db.get_value("Employee", {"user_id": user, "status": ("!=", "Left")}, fields, as_dict=True) or {}
			mobile = next((str(employee.get(field) or "").strip() for field in fields if employee.get(field)), "")
	if not mobile:
		frappe.throw(
			_("Add a mobile number to your User or Employee record before requesting an approval OTP."),
			frappe.PermissionError,
		)
	from ch_item_master.ch_item_master.utils import validate_indian_phone

	return validate_indian_phone(mobile, _("Approver Mobile"))


def _trusted_otp_mobile(supplied_mobile=None) -> str:
	trusted = _approver_mobile(frappe.session.user)
	if supplied_mobile:
		from ch_item_master.ch_item_master.utils import validate_indian_phone

		supplied = validate_indian_phone(supplied_mobile, _("Approver Mobile"))
		if supplied != trusted:
			frappe.throw(
				_("The OTP mobile must match the authenticated approver's registered mobile number."),
				frappe.PermissionError,
			)
	return trusted


def _assert_exception_scope(company, store_warehouse=None, pos_profile=None) -> None:
	ensure_company_access(company)
	if store_warehouse:
		warehouse_company = frappe.db.get_value("Warehouse", store_warehouse, "company")
		if not warehouse_company or warehouse_company != company:
			frappe.throw(_("The selected warehouse does not belong to the exception company."), frappe.PermissionError)
		try:
			from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
		except ImportError:
			if not is_privileged_user():
				frappe.throw(_("Store scope cannot be verified."), frappe.PermissionError)
		else:
			assert_user_has_store_scope(warehouse=store_warehouse, company=company)

	if pos_profile:
		profile = frappe.db.get_value("POS Profile", pos_profile, ["company", "warehouse"], as_dict=True)
		if not profile or profile.company != company:
			frappe.throw(_("The POS Profile does not belong to the exception company."), frappe.PermissionError)
		if store_warehouse and profile.warehouse and profile.warehouse != store_warehouse:
			frappe.throw(_("The POS Profile warehouse does not match the exception warehouse."), frappe.PermissionError)


# ─────────────────────────────────────────────────────────────────────────────
# Core APIs — whitelisted for POS / frontend consumption
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def raise_exception(exception_type, company, reason, requested_value=0,
                    original_value=0, reference_doctype=None, reference_name=None,
                    item_code=None, serial_no=None, store_warehouse=None,
                    pos_profile=None, pos_invoice=None, customer=None) -> dict:
	"""Create a new CH Exception Request in Pending state.

	Returns dict with request name + exception type config (for frontend
	to decide whether to show OTP dialog, call manager, etc.).
	"""
	_assert_exception_scope(company, store_warehouse, pos_profile)
	etype = frappe.get_cached_doc("CH Exception Type", exception_type)
	if not etype.enabled:
		frappe.throw(_("Exception type {0} is disabled").format(exception_type), title=_("API Error"))

	frappe.has_permission("CH Exception Request", "create", throw=True)
	customer = (customer or "").strip() or None
	if reference_doctype or reference_name:
		if not reference_doctype or not reference_name:
			frappe.throw(_("Reference DocType and document must be provided together."), frappe.ValidationError)
		reference_doc = frappe.get_doc(reference_doctype, reference_name)
		reference_doc.check_permission("read")
		if reference_doc.meta.has_field("company") and reference_doc.get("company") not in (None, "", company):
			frappe.throw(_("The referenced document belongs to another company."), frappe.PermissionError)
	if customer:
		frappe.has_permission("Customer", "read", customer, throw=True)
	if item_code:
		frappe.has_permission("Item", "read", item_code, throw=True)

	# ── Customer identity is mandatory for POS-raised exceptions ──────────
	# Doctrine (Oracle Xstore / SAP CAR-POS / Dynamics 365 Commerce): a
	# discount / price override / return-beyond-policy approval is a
	# CUSTOMER-scoped grant, not a store-scoped bearer token. Without a
	# customer identity, the approved exception becomes a floating
	# credit that can be reused across bills / walk-ins — that is exactly
	# what happened in prod (CHEXC raised on bill A, silently consumed on
	# bill B for a different customer).
	#
	# Enforce here (server = source of truth) so no client bypass is
	# possible. The walk-in customer configured on the POS Profile is
	# treated as "no customer" because it's an anonymous placeholder, not
	# an identity.
	walk_in_customer = None
	if pos_profile:
		walk_in_customer = (
			frappe.db.get_value("POS Profile", pos_profile, "customer") or None
		)
	if pos_profile and (not customer or (walk_in_customer and customer == walk_in_customer)):
		frappe.throw(
			_("Select a real customer before raising an exception. "
			  "The default walk-in customer cannot own an approval — "
			  "approvals are customer-scoped and cannot be reused across bills."),
			title=_("Customer Required"),
		)

	# IM-12 fix: Prevent duplicate open exception requests for same IMEI+store+type.
	# Fallback to item-level only when IMEI is not available.
	if store_warehouse and (serial_no or item_code):
		dup_filters = {
			"exception_type": exception_type,
			"store_warehouse": store_warehouse,
			"status": ("in", ["Pending", "Awaiting Approval"]),
		}
		dup_scope = _("item")
		dup_value = item_code

		if serial_no:
			dup_filters["serial_no"] = serial_no
			dup_scope = _("IMEI")
			dup_value = serial_no
		else:
			dup_filters["item_code"] = item_code

		existing = frappe.db.exists("CH Exception Request", dup_filters)
		if existing:
			frappe.throw(
				_("An open exception request ({0}) already exists for {1} {2} at this store. "
				  "Please wait for it to be resolved.").format(existing, dup_scope, dup_value),
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
	if customer and not exc.customer_phone:
		cust = frappe.db.get_value(
			"Customer",
			customer,
			["mobile_no", "ch_alternate_phone"],
			as_dict=True,
		)
		if cust:
			exc.customer_phone = cust.mobile_no or cust.ch_alternate_phone or ""

	# Auto-approve short-circuit — value within policy threshold needs no approver.
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
		exc._authorize_approval_transition()
		exc.insert()
		exc.submit()
		return {
			"name": exc.name,
			"status": "Auto-Approved",
			"approval_expiry": str(exc.approval_expiry),
		}

	# Routing — single source of truth = CH Approval Authority matrix.
	# Resolves exc.category / exc.approval_role / exc.assigned_approver in place.
	resolve_exception_approver(exc, etype)

	exc._authorize_approval_transition()
	exc.insert()

	# Alert the responsible team, the assigned approver, and their manager
	# (best-effort; never block the request).
	try:
		_notify_exception_raised(exc, etype)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"CH Exception Request raise-notification failed for {exc.name}",
		)

	return {
		"name": exc.name,
		"status": "Pending",
		"requires_otp": bool(etype.requires_otp),
		"requires_ho_approval": bool(etype.requires_ho_approval),
		"routing_mode": etype.routing_mode or "Approval Matrix",
		"approval_role": exc.approval_role,
		"assigned_approver": exc.assigned_approver,
		"category": exc.category,
	}


# ─────────────────────────────────────────────────────────────────────────────
# Approver routing — matrix-driven (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

_AUTH_ACTION = "Approve"
_AUTH_DOCTYPE = "CH Exception Request"


def _authority():
	"""Return the ch_erp15 authority engine, or None if unavailable.

	Cross-app dependency is optional — if ch_erp15 is not installed the
	matrix simply yields no routing and the request stays unassigned (any
	authorised manager can still action it).
	"""
	try:
		from ch_erp15.ch_erp15.auth import authority
		return authority
	except Exception:
		return None


def resolve_exception_approver(exc, etype) -> None:
	"""Resolve who must approve `exc` and record it on the document.

	Sets ``exc.category`` (from the item), ``exc.approval_role`` (the matrix
	band) and ``exc.assigned_approver`` (the resolved person). Two modes:

	* **Category Manager** — person-routed to the item's CH Category manager.
	* **Approval Matrix** (default) — the value band → role is read from
	  CH Approval Authority and the role → person from CH User Scope.
	"""
	mode = etype.routing_mode or "Approval Matrix"

	# Best-effort category resolution (used for routing + condition eval).
	if exc.item_code and not exc.category:
		exc.category = frappe.db.get_value("Item", exc.item_code, "ch_category") or None

	if mode == "Category Manager":
		_route_to_category_manager(exc)
		return

	# Approval Matrix mode.
	auth = _authority()
	if not auth:
		return
	role = auth.required_role_for_amount(
		_AUTH_ACTION, _AUTH_DOCTYPE, flt(exc.requested_value), doc=exc
	)
	if not role:
		# No band matched — leave unassigned (graceful); team alert still fires.
		return
	exc.approval_role = role
	exc.assigned_approver = _apply_delegation(
		_resolve_user_by_scope(role, exc.store_warehouse)
	)


def _route_to_category_manager(exc) -> None:
	"""Legacy person-routing: item → CH Category → category_manager."""
	if not exc.item_code:
		frappe.throw(
			_("Category Manager routing requires an item. Please raise this exception from a cart line."),
			title=_("Item Required"),
		)
	category = exc.category or frappe.db.get_value("Item", exc.item_code, "ch_category")
	if not category:
		frappe.throw(
			_("Item {0} is not mapped to a CH Category. Cannot route to a Category Manager.").format(exc.item_code),
			title=_("Category Not Set"),
		)
	manager = frappe.db.get_value("CH Category", category, "category_manager")
	if not manager:
		frappe.throw(
			_("No Category Manager is configured for category {0}. Please set 'Category Manager' on CH Category {0}.").format(category),
			title=_("Category Manager Missing"),
		)
	exc.category = category
	exc.assigned_approver = _apply_delegation(manager)


def _resolve_user_by_scope(role, store):
	"""Resolve a role to a single user, preferring those scoped to `store`.

	Mirrors the SAP responsibility-rule / Oracle position-hierarchy pattern:
	the approver is the holder of `role` whose CH User Scope covers the store.
	Falls back to any enabled role-holder when no scoped user exists.
	"""
	if not role:
		return None
	users = []
	try:
		from ch_erp15.ch_erp15.notification_router import get_scoped_users
		users = get_scoped_users([role], store=store) or []
	except Exception:
		users = []
	if not users:
		users = _users_with_role(role)
	return users[0] if users else None


def _users_with_role(role):
	"""Enabled business users holding `role`."""
	return get_enabled_role_users((role,))


def _apply_delegation(user):
	"""Forward delegation: if `user` has an active CH Approval Delegation,
	route to the delegate instead (vacation / out-of-office substitution)."""
	if not user:
		return user
	row = frappe.db.sql(
		"""
			SELECT delegation.`delegate`
			FROM `tabCH Approval Delegation` delegation
			INNER JOIN `tabUser` delegate_user
			  ON delegate_user.`name` = delegation.`delegate`
			 AND delegate_user.`enabled` = 1
			 AND delegate_user.`user_type` = 'System User'
			WHERE delegation.`delegator` = %(user)s
			  AND delegation.`active` = 1
			  AND delegation.`delegate` IS NOT NULL
			  AND delegation.`delegate` != %(user)s
			  AND (delegation.`valid_from` IS NULL OR delegation.`valid_from` <= %(today)s)
			  AND (delegation.`valid_to` IS NULL OR delegation.`valid_to` >= %(today)s)
			ORDER BY COALESCE(delegation.`valid_from`, '1000-01-01') DESC,
			         delegation.`modified` DESC,
			         delegation.`name` ASC
			LIMIT 1
		""",
		{"user": user, "today": getdate()},
	)
	return row[0][0] if row else user


def _manager_role_for(exc):
	"""The role one band ABOVE the assigned band — i.e. the approver's manager."""
	auth = _authority()
	if not auth or not exc.approval_role:
		return None
	cur_max = auth.max_amount_for_role(_AUTH_ACTION, _AUTH_DOCTYPE, exc.approval_role)
	if cur_max == float("inf"):
		return None  # already top of the ladder
	return auth.next_band_above(_AUTH_ACTION, _AUTH_DOCTYPE, cur_max)


def _notify_exception_raised(exc, etype, escalated: bool = False) -> None:
	"""Alert the responsible team, the assigned approver, and their manager.

	Recipient model (MS Dynamics 365 assigned-to + escalation-owner):
	  • assigned approver — the matrix/category-resolved person who must act;
	  • team              — holders of ``notify_team_role`` scoped to the store;
	  • manager           — holders of the next band-up role scoped to the store.
	The requester is excluded. Best-effort: missing recipients are skipped.
	"""
	store = exc.store_warehouse
	company = exc.get("company")
	recipient_users: set[str] = set()

	if exc.assigned_approver:
		recipient_users.add(exc.assigned_approver)

	team_role = etype.notify_team_role or etype.alert_role
	if team_role:
		recipient_users.update(_scoped_role_users(team_role, store, company))

	mgr_role = _manager_role_for(exc)
	if mgr_role:
		recipient_users.update(_scoped_role_users(mgr_role, store, company))

	# Resolve to emails; drop the requester and blanks.
	emails: set[str] = set()
	for u in recipient_users:
		if u == exc.requested_by:
			continue
		email = frappe.db.get_value("User", u, "email")
		if email:
			emails.add(email)
	if not emails:
		return

	heading = _("Exception Escalated") if escalated else _("Exception Approval Required")
	subject = _("{0} | {1} | {2}").format(heading, exc.exception_type, exc.name)
	frappe.sendmail(
		recipients=sorted(emails),
		subject=subject,
		message=_build_exception_email(exc, heading),
		reference_doctype="CH Exception Request",
		reference_name=exc.name,
	)


def _scoped_role_users(role, store, company=None):
	"""Holders of `role` scoped to `store` and `company` — FAIL-CLOSED.

	This used to fall back to every holder of the role when the scoped lookup
	came back empty, which turned a missing CH User Scope row into a site-wide,
	cross-company blast. An unresolvable scope must narrow, never widen: the
	assigned approver is added by the caller regardless, so the exception is
	never left with nobody to action it.
	"""
	try:
		from ch_erp15.ch_erp15.notification_router import (
			filter_users_by_company,
			get_scoped_users,
		)

		return filter_users_by_company(get_scoped_users([role], store=store) or [], company)
	except Exception:
		frappe.log_error(title=f"Exception notify: scope resolution failed for role {role}")
		return []


def _build_exception_email(exc, heading) -> str:
	"""Render the rich approval email (device + pricing + request details)."""
	def fmt_currency(value):
		val = flt(value or 0)
		return f"₹{val:,.2f}" if val else "—"

	original_price = flt(exc.original_value or 0)
	requested_price = flt(exc.requested_value or 0)
	discount_amount = max(0, original_price - requested_price)
	discount_pct = (discount_amount / original_price * 100) if original_price > 0 else 0

	pricing_table = (
		"<table style='width:100%; border-collapse: collapse; margin: 10px 0;'>"
		"<tr style='background-color: #f5f5f5;'>"
		"<td style='border: 1px solid #ddd; padding: 8px; font-weight: bold;'>Original Selling Price</td>"
		f"<td style='border: 1px solid #ddd; padding: 8px; text-align: right;'>{fmt_currency(original_price)}</td>"
		"</tr>"
		"<tr>"
		"<td style='border: 1px solid #ddd; padding: 8px; font-weight: bold;'>Requested Price</td>"
		f"<td style='border: 1px solid #ddd; padding: 8px; text-align: right;'>{fmt_currency(requested_price)}</td>"
		"</tr>"
		"<tr style='background-color: #fff3cd;'>"
		"<td style='border: 1px solid #ddd; padding: 8px; font-weight: bold;'>Discount</td>"
		f"<td style='border: 1px solid #ddd; padding: 8px; text-align: right;'>{fmt_currency(discount_amount)} ({discount_pct:.1f}%)</td>"
		"</tr>"
		"<tr>"
		"<td style='border: 1px solid #ddd; padding: 8px; font-weight: bold;'>Purchase Cost</td>"
		f"<td style='border: 1px solid #ddd; padding: 8px; text-align: right;'>{fmt_currency(exc.purchase_price)}</td>"
		"</tr>"
		"</table>"
	)

	role_line = ""
	if exc.approval_role:
		role_line = _("<li><b>Approval Band:</b> {0}</li>").format(exc.approval_role)

	return _(
		"<p><b>{0}</b></p>"
		"<p>Exception request <b>{1}</b> requires action.</p>"
		"<hr style='margin: 15px 0;'>"
		"<p style='font-size: 14px; margin-bottom: 10px;'><b>📱 Device Details</b></p>"
		"<ul style='margin: 10px 0; padding-left: 20px;'>"
		"<li><b>Item:</b> {2} ({3})</li>"
		"<li><b>Serial No (IMEI):</b> {4}</li>"
		"<li><b>Category:</b> {5}</li>"
		"</ul>"
		"<p style='font-size: 14px; margin-bottom: 10px;'><b>💰 Pricing Details</b></p>"
		"{6}"
		"<p style='font-size: 14px; margin-bottom: 10px;'><b>👤 Request Information</b></p>"
		"<ul style='margin: 10px 0; padding-left: 20px;'>"
		"<li><b>Type:</b> {7}</li>"
		"{8}"
		"<li><b>Customer:</b> {9}</li>"
		"<li><b>Requested By:</b> {10}</li>"
		"<li><b>Reason:</b> {11}</li>"
		"</ul>"
		"<p><a href='{12}' style='display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 4px;'>"
		"👉 Review & Approve in ERPNext</a></p>"
		"<p style='color: #666; font-size: 12px;'><em>CH Exception Request {1}</em></p>"
	).format(
		heading,
		exc.name,
		exc.item_name or "",
		exc.item_code or "",
		exc.serial_no or "-",
		exc.category or "-",
		pricing_table,
		exc.exception_type or "",
		role_line,
		exc.customer_name or exc.customer or "-",
		exc.requested_by_name or exc.requested_by or "",
		exc.requested_reason or "",
		frappe.utils.get_url(f"/app/ch-exception-request/{exc.name}"),
	)


@frappe.whitelist(methods=["POST"])
def approve_exception(exception_name, approver_user=None, channel=None,
                      otp_code=None, otp_mobile=None,
                      resolution_value=None, remarks=None) -> dict:
	"""Approve an exception request.

	If the exception type requires OTP, validates it first.
	Enforces Segregation of Duties — the user who raised the exception
	cannot also approve it (System Manager bypass is audited).
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	frappe.has_permission("CH Exception Request", "write", doc=exc, throw=True)
	if exc.status not in ("Pending", "Escalated"):
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status), title=_("API Error"))

	if exc.docstatus == 1:
		frappe.throw(_("Exception {0} is already submitted").format(exception_name), title=_("API Error"))

	etype = frappe.get_cached_doc("CH Exception Type", exc.exception_type)
	if approver_user and approver_user != frappe.session.user:
		frappe.throw(
			_("Approver identity is derived from the authenticated session."),
			frappe.PermissionError,
		)
	approver_user = frappe.session.user

	# SoD enforcement — the requester cannot approve their own exception.
	from ch_item_master.ch_item_master.rbac import check_sod
	check_sod(submitted_by=exc.requested_by, approver=approver_user)

	# OTP validation
	otp_ref = None
	if etype.requires_otp:
		if not otp_code:
			frappe.throw(_("OTP is mandatory for this exception type."), frappe.AuthenticationError)
		otp_mobile = _trusted_otp_mobile(otp_mobile)
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
		channel="OTP" if otp_ref else (channel or "Manager PIN"),
		otp_reference=otp_ref,
		resolution_value=resolution_value,
		remarks=remarks,
	)

	return {
		"name": exc.name,
		"status": "Approved",
		"approval_expiry": str(exc.approval_expiry),
	}


@frappe.whitelist(methods=["POST"])
def reject_exception(exception_name, reason=None) -> dict:
	"""Reject an exception request."""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	frappe.has_permission("CH Exception Request", "write", doc=exc, throw=True)
	if exc.status not in ("Pending", "Escalated"):
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status), title=_("API Error"))

	exc.reject(reason=reason)
	return {"name": exc.name, "status": "Rejected"}


@frappe.whitelist()
def check_exception_valid(exception_name) -> dict:
	"""Check if an approved exception is still within its validity window.

	Called by POS / other apps before consuming the exception.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	frappe.has_permission("CH Exception Request", "read", doc=exc, throw=True)
	valid = exc.is_valid()
	# Diagnose WHY the exception is not valid so the client can surface an
	# actionable message instead of a bare "no longer valid". Kept in sync
	# with CHExceptionRequest.is_valid so any new guard there flows through.
	invalid_reason = None
	if not valid:
		if exc.status not in ("Approved", "Auto-Approved") or exc.docstatus != 1:
			invalid_reason = "not_approved"
		elif exc.approval_expiry and now_datetime() > exc.approval_expiry:
			invalid_reason = "expired"
		elif exc.pos_profile and exc.raised_at and getdate(exc.raised_at) != getdate():
			invalid_reason = "different_day"
		else:
			invalid_reason = "unknown"
	return {
		"name": exc.name,
		"valid": valid,
		"invalid_reason": invalid_reason,
		"status": exc.status,
		"approval_expiry": str(exc.approval_expiry) if exc.approval_expiry else None,
		"raised_at": str(exc.raised_at) if exc.raised_at else None,
		"pos_profile": exc.pos_profile,
		"item_code": exc.item_code,
		"serial_no": exc.serial_no,
		"customer": exc.customer,
		"customer_name": exc.customer_name,
		"customer_phone": exc.customer_phone,
		"original_value": flt(exc.original_value),
		"requested_value": flt(exc.requested_value),
		"resolution_value": flt(exc.resolution_value),
		"exception_type": exc.exception_type,
	}


@frappe.whitelist(methods=["POST"])
def request_exception_otp(exception_name, mobile_no=None) -> dict:
	"""Generate an OTP for an exception request approval.

	Uses the exception type as the OTP purpose.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	frappe.has_permission("CH Exception Request", "write", doc=exc, throw=True)
	exc._validate_approver(frappe.session.user)
	if exc.status not in ("Pending", "Escalated"):
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status), title=_("API Error"))
	if not frappe.get_cached_value("CH Exception Type", exc.exception_type, "requires_otp"):
		frappe.throw(_("This exception type does not require OTP approval."), frappe.ValidationError)
	mobile_no = _trusted_otp_mobile(mobile_no)

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
	return {"otp_sent": True, "mobile_no": f"******{mobile_no[-4:]}"}


# ─────────────────────────────────────────────────────────────────────────────
# Query / Report helpers
# ─────────────────────────────────────────────────────────────────────────────

# Exception types that belong to the cycle-count / inventory audit workflow,
# NOT the cashier "override the bill" workflow. Keeping these out of the POS
# Exceptions tab matches the market-standard separation (SAP CAR / Oracle
# Retail / Dynamics 365 Commerce): price/discount overrides live in the POS
# transaction context; stock variance approvals live in the audit workspace.
_CYCLE_COUNT_EXCEPTION_TYPES = ("Stock Count Variance",)


@frappe.whitelist()
def get_pending_exceptions(
	company=None, store_warehouse=None, exception_type=None, scope="bill"
) -> list:
	"""Return pending and recently-approved exception requests for the current user.

	``scope`` filters by request family so the two workspaces don't bleed
	into each other:
	- ``"bill"`` (default) – cashier overrides only (Discount Override,
	  Free Accessory, Below Margin Sale, Return Beyond Policy, …).
	  Excludes Stock Count Variance / any CH Cycle Count-sourced request.
	- ``"cycle_count"`` – only Stock Count Variance requests (Stock Audit
	  workspace).
	- ``"all"`` – no scope filter (admin / legacy callers).
	"""
	user = frappe.session.user
	scope = (scope or "bill").strip().lower()
	filters = {
		"requested_by": user,
		"status": ("in", ["Pending", "Escalated", "Awaiting Approval", "Approved", "Auto-Approved"]),
		"docstatus": ("!=", 2),
	}
	or_filters = []
	if company:
		filters["company"] = company
	if store_warehouse:
		filters["store_warehouse"] = store_warehouse
	if exception_type:
		filters["exception_type"] = exception_type
	elif scope == "cycle_count":
		filters["exception_type"] = ("in", list(_CYCLE_COUNT_EXCEPTION_TYPES))
	elif scope == "bill":
		filters["exception_type"] = ("not in", list(_CYCLE_COUNT_EXCEPTION_TYPES))

	if scope == "bill":
		filters["raised_at"] = (">=", add_to_date(now_datetime(), hours=-24))
		or_filters = [
			["pos_invoice", "is", "not set"],
			["pos_invoice", "=", ""],
		]
	# scope == "all" → no exception_type clause

	query_args = {
		"filters": filters,
		"fields": [
			"name", "exception_type", "company", "store_warehouse",
			"requested_by", "requested_by_name", "requested_reason",
			"requested_value", "original_value", "resolution_value",
			"reference_doctype", "reference_name",
			"item_code", "serial_no", "raised_at", "status", "pos_invoice",
		],
		"order_by": "raised_at desc",
		"limit_page_length": min(get_int_setting("exception_query_limit", 50, minimum=1), 500),
	}
	if or_filters:
		query_args["or_filters"] = or_filters

	return frappe.get_all("CH Exception Request", **query_args)


@frappe.whitelist()
def get_exception_summary(company, from_date=None, to_date=None) -> list:
	"""Return aggregated exception stats for reporting / dashboard."""
	require_role_setting(
		"exception_approval_roles",
		_EXCEPTION_SUMMARY_ROLES,
		action="view exception summaries",
	)
	if not is_privileged_user() and not frappe.has_permission(
		"CH Exception Request",
		ptype="read",
		user=frappe.session.user,
	):
		frappe.throw(
			_("You do not have read permission for CH Exception Request."),
			frappe.PermissionError,
		)

	company = (company or "").strip()
	if not company:
		frappe.throw(_("Company is required."), frappe.ValidationError)
	get_company_scope(requested_company=company)
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
			msg=_("You are not permitted to view exception data for this company."),
		)

	try:
		from_date = getdate(from_date or nowdate())
		to_date = getdate(to_date or nowdate())
	except (TypeError, ValueError):
		frappe.throw(_("Enter valid From Date and To Date values."), frappe.ValidationError)
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."), frappe.ValidationError)
	if (to_date - from_date).days > _MAX_EXCEPTION_SUMMARY_DAYS:
		frappe.throw(
			_("The exception summary date range cannot exceed {0} days.").format(
				_MAX_EXCEPTION_SUMMARY_DAYS
			),
			frappe.ValidationError,
		)

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
		WHERE company = %(company)s
		  AND DATE(raised_at) BETWEEN %(from_date)s AND %(to_date)s
		  AND docstatus != 2
		GROUP BY exception_type, status
		ORDER BY exception_type, status
		LIMIT 500
	""", {
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
	}, as_dict=True)

	return data


# ─────────────────────────────────────────────────────────────────────────────
# Scheduled: SLA escalation, then hard expiry
# ─────────────────────────────────────────────────────────────────────────────

def escalate_pending_exceptions():
	"""Escalate SLA-breached Pending requests to the next matrix band up.

	Market-standard escalate-then-expire ladder (SAP deadlines / Oracle timeout /
	D365 escalation owner). For each Pending/Escalated request whose exception
	type defines an ``escalation_sla_minutes`` and that has waited longer than
	that window since it was raised (or last escalated), the assigned band is
	moved up one level, the approver is re-resolved, and the team + new approver
	+ their manager are re-alerted. Requests already at the top band are left for
	the 24h hard expiry. Runs hourly.
	"""
	auth = _authority()
	if not auth:
		return

	now = now_datetime()
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 2000)
	rows = frappe.get_all("CH Exception Request",
		filters={"status": ("in", ["Pending", "Escalated"]), "docstatus": 0},
		fields=["name", "exception_type", "approval_role", "raised_at", "last_escalated_at"],
		order_by="raised_at asc, name asc",
		limit=batch_limit + 1,
	)
	candidates = rows[:batch_limit]

	escalated = 0
	failed = 0
	for index, row in enumerate(candidates):
		save_point = f"exception_escalation_{index}"
		frappe.db.savepoint(save_point)
		try:
			if not row.approval_role:
				continue
			etype = frappe.get_cached_doc("CH Exception Type", row.exception_type)
			sla = etype.escalation_sla_minutes or 0
			if sla <= 0:
				continue
			anchor = row.last_escalated_at or row.raised_at
			if not anchor or now < add_to_date(anchor, minutes=sla):
				continue

			cur_max = auth.max_amount_for_role(_AUTH_ACTION, _AUTH_DOCTYPE, row.approval_role)
			if cur_max == float("inf"):
				continue  # already top band — leave for hard expiry
			next_role = auth.next_band_above(_AUTH_ACTION, _AUTH_DOCTYPE, cur_max)
			if not next_role or next_role == row.approval_role:
				continue

			exc = frappe.get_doc("CH Exception Request", row.name)
			prior_role = exc.approval_role
			exc.approval_role = next_role
			exc.assigned_approver = _apply_delegation(
				_resolve_user_by_scope(next_role, exc.store_warehouse)
			)
			exc.status = "Escalated"
			exc.last_escalated_at = now
			exc._authorize_approval_transition()
			exc.save(ignore_permissions=True)

			try:
				_notify_exception_raised(exc, etype, escalated=True)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"Escalation alert failed for {exc.name}",
				)
			exc._write_audit_log(
				before=f"Band {prior_role}",
				after=f"Escalated to {next_role}",
				remarks=f"SLA breach ({sla} min) auto-escalation",
				event_type="Other",
			)
			escalated += 1
		except Exception:
			frappe.db.rollback(save_point=save_point)
			failed += 1
			frappe.log_error(
				frappe.get_traceback(),
				f"Exception escalation failed for {row.name}",
			)
	return {
		"evaluated": len(candidates),
		"escalated": escalated,
		"failed": failed,
		"has_more": len(rows) > batch_limit,
	}


def expire_stale_exceptions():
	"""Mark old pending/escalated exceptions as Expired.

	Called via scheduler (hourly). An exception that has been pending for
	more than 24 hours — after exhausting SLA escalation — is auto-expired.
	"""
	expiry_hours = get_int_setting("exception_expiry_hours", 24, minimum=1)
	cutoff = add_to_date(now_datetime(), hours=-expiry_hours)
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 2000)
	rows = frappe.get_all("CH Exception Request",
		filters={
			"status": ("in", ["Pending", "Escalated"]),
			"docstatus": 0,
			"raised_at": ("<=", cutoff),
		},
		pluck="name",
		order_by="raised_at asc, name asc",
		limit=batch_limit + 1,
	)
	stale = rows[:batch_limit]

	expired = 0
	failed = 0
	for index, name in enumerate(stale):
		save_point = f"exception_expiry_{index}"
		frappe.db.savepoint(save_point)
		try:
			exc = frappe.get_doc("CH Exception Request", name)
			exc.status = "Expired"
			exc.resolved_at = now_datetime()
			exc.resolution_remarks = f"Auto-expired after {expiry_hours} hours"
			exc._authorize_approval_transition()
			exc.save(ignore_permissions=True)
			exc.submit()
			expired += 1
		except Exception:
			frappe.db.rollback(save_point=save_point)
			failed += 1
			frappe.log_error(
				frappe.get_traceback(),
				f"Failed to expire exception {name}",
			)
	return {
		"expired": expired,
		"failed": failed,
		"has_more": len(rows) > batch_limit or bool(failed),
	}

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
