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
		exc.insert(ignore_permissions=True)
		exc.submit()
		return {
			"name": exc.name,
			"status": "Auto-Approved",
			"approval_expiry": str(exc.approval_expiry),
		}

	# Routing — single source of truth = CH Approval Authority matrix.
	# Resolves exc.category / exc.approval_role / exc.assigned_approver in place.
	resolve_exception_approver(exc, etype)

	exc.insert(ignore_permissions=True)

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
	"""Enabled, real users holding `role` (excludes Administrator/Guest)."""
	rows = frappe.get_all(
		"Has Role",
		filters={"role": role, "parenttype": "User"},
		pluck="parent",
	)
	return [
		u for u in rows
		if u not in ("Administrator", "Guest")
		and frappe.db.get_value("User", u, "enabled")
	]


def _apply_delegation(user):
	"""Forward delegation: if `user` has an active CH Approval Delegation,
	route to the delegate instead (vacation / out-of-office substitution)."""
	if not user:
		return user
	today_d = getdate()
	for d in frappe.get_all(
		"CH Approval Delegation",
		filters={"delegator": user, "active": 1},
		fields=["delegate", "valid_from", "valid_to"],
	):
		if d.valid_from and getdate(d.valid_from) > today_d:
			continue
		if d.valid_to and getdate(d.valid_to) < today_d:
			continue
		if d.delegate and d.delegate != user:
			return d.delegate
	return user


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
	recipient_users: set[str] = set()

	if exc.assigned_approver:
		recipient_users.add(exc.assigned_approver)

	team_role = etype.notify_team_role or etype.alert_role
	if team_role:
		recipient_users.update(_scoped_or_role(team_role, store))

	mgr_role = _manager_role_for(exc)
	if mgr_role:
		recipient_users.update(_scoped_or_role(mgr_role, store))

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


def _scoped_or_role(role, store):
	"""Store-scoped holders of `role`, falling back to all role-holders."""
	try:
		from ch_erp15.ch_erp15.notification_router import get_scoped_users
		users = get_scoped_users([role], store=store) or []
	except Exception:
		users = []
	return users or _users_with_role(role)


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


@frappe.whitelist()
def approve_exception(exception_name, approver_user=None, channel=None,
                      otp_code=None, otp_mobile=None,
                      resolution_value=None, remarks=None) -> dict:
	"""Approve an exception request.

	If the exception type requires OTP, validates it first.
	Enforces Segregation of Duties — the user who raised the exception
	cannot also approve it (System Manager bypass is audited).
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status not in ("Pending", "Escalated"):
		frappe.throw(_("Exception {0} is already {1}").format(exception_name, exc.status), title=_("API Error"))

	frappe.has_permission("CH Exception Request", "write", throw=True)

	if exc.docstatus == 1:
		frappe.throw(_("Exception {0} is already submitted").format(exception_name), title=_("API Error"))

	etype = frappe.get_cached_doc("CH Exception Type", exc.exception_type)
	approver_user = approver_user or frappe.session.user

	# SoD enforcement — the requester cannot approve their own exception.
	from ch_item_master.ch_item_master.rbac import check_sod
	check_sod(submitted_by=exc.requested_by, approver=approver_user)

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
	if exc.status not in ("Pending", "Escalated"):
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


@frappe.whitelist()
def request_exception_otp(exception_name, mobile_no) -> dict:
	"""Generate an OTP for an exception request approval.

	Uses the exception type as the OTP purpose.
	"""
	exc = frappe.get_doc("CH Exception Request", exception_name)
	if exc.status not in ("Pending", "Escalated"):
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
	filters = {
		"requested_by": user,
		"status": ("in", ["Pending", "Escalated", "Awaiting Approval", "Approved", "Auto-Approved"]),
		"docstatus": ("!=", 2),
	}
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
	# scope == "all" → no exception_type clause

	return frappe.get_all("CH Exception Request",
		filters=filters,
		fields=[
			"name", "exception_type", "company", "store_warehouse",
			"requested_by", "requested_by_name", "requested_reason",
			"requested_value", "original_value", "resolution_value",
			"reference_doctype", "reference_name",
			"item_code", "serial_no", "raised_at", "status", "pos_invoice",
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
	candidates = frappe.get_all("CH Exception Request",
		filters={"status": ("in", ["Pending", "Escalated"]), "docstatus": 0},
		fields=["name", "exception_type", "approval_role", "raised_at", "last_escalated_at"],
		limit=500,
	)

	escalated = 0
	for row in candidates:
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
			frappe.log_error(
				frappe.get_traceback(),
				f"Exception escalation failed for {row.name}",
			)

	if escalated:
		frappe.db.commit()


def expire_stale_exceptions():
	"""Mark old pending/escalated exceptions as Expired.

	Called via scheduler (hourly). An exception that has been pending for
	more than 24 hours — after exhausting SLA escalation — is auto-expired.
	"""
	cutoff = add_to_date(now_datetime(), hours=-24)
	stale = frappe.get_all("CH Exception Request",
		filters={
			"status": ("in", ["Pending", "Escalated"]),
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