# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Item Master RBAC Module — Oracle/SAP-parity access control.

Implements:
  1. Item company scoping      — permission_query_condition for Item list
  2. Granular role gates       — check_plm_role(), check_vendor_manager_role(),
                                 check_gtin_editor_role(), check_item_creator_role()
  3. Segregation of Duties     — check_sod(submitted_by, approver)
  4. Approval Delegation       — is_effective_approver(user)
  5. Custom DocPerm install    — permlevel-1 on pricing / sensitive fields
  6. Time-bound role expiry    — expire_role_assignments() (daily scheduled task)
  7. Break-glass access log    — open_break_glass(reason), close_break_glass(name)
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, today

from ch_item_master.config import (
	get_enabled_system_role_emails,
	get_int_setting,
	get_role_setting,
	is_privileged_user,
)

# Sensitive Item custom fields that get permlevel=1
_SENSITIVE_FIELDS = [
	"ch_standard_cost",
	"ch_standard_cost_updated_on",
	"ch_minimum_selling_price",
	"ch_msp_effective_from",
	"ch_gtin",
]

SoDError    = frappe.ValidationError
RoleGateError = frappe.ValidationError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _roles(user: str | None = None) -> frozenset[str]:
	return frozenset(frappe.get_roles(user or frappe.session.user))


def _has_configured_role(fieldname: str, defaults, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if is_privileged_user(user):
		return True
	return bool(_roles(user) & get_role_setting(fieldname, defaults))


def _configured_role_users(fieldname: str, defaults) -> list[str]:
	roles = get_role_setting(fieldname, defaults)
	return get_enabled_system_role_emails(roles)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Item Company Scoping (permission_query_condition)
# ─────────────────────────────────────────────────────────────────────────────

def get_item_query(user: str) -> str:
	"""
	Returns a SQL WHERE fragment that restricts Item list view to the user's
	allowed companies (resolved via ch_item_master.security).

	Items with no tabItem Default rows are visible to all users (global items).
	Returns "" for configured supervisor roles and Administrator.
	"""
	if not user:
		user = frappe.session.user
	if _has_configured_role("break_glass_supervisor_roles", ("System Manager",), user):
		return ""

	try:
		from ch_item_master.security import get_user_allowed_companies
		companies = get_user_allowed_companies(user)
	except Exception:
		return "1=0"

	if companies is None:
		return ""
	if not companies:
		return "1=0"

	quoted = ", ".join(frappe.db.escape(c) for c in companies)
	return (
		f"(`tabItem`.`name` IN ("
		f"  SELECT `parent` FROM `tabItem Default`"
		f"  WHERE `company` IN ({quoted})"
		f") OR NOT EXISTS ("
		f"  SELECT 1 FROM `tabItem Default`"
		f"  WHERE `parent` = `tabItem`.`name`"
		f"))"
	)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Granular Role Gates
# ─────────────────────────────────────────────────────────────────────────────

def check_plm_role(user: str | None = None) -> None:
	"""Raise if user doesn't have the CH PLM Manager (or higher) role."""
	if not _has_configured_role(
		"plm_manager_roles",
		("CH PLM Manager", "CH Master Approver", "CH Master Manager", "System Manager"),
		user,
	):
		frappe.throw(
			_("CH PLM Manager role is required to change the PLM status of an item."),
			title=_("PLM Role Required"),
			exc=RoleGateError,
		)


def check_vendor_manager_role(user: str | None = None) -> None:
	"""Raise if user doesn't have the CH Vendor Manager (or higher) role."""
	if not _has_configured_role(
		"vendor_manager_roles",
		("CH Vendor Manager", "CH Master Manager", "System Manager"),
		user,
	):
		frappe.throw(
			_("CH Vendor Manager role is required to create or update Vendor Info Records."),
			title=_("Vendor Manager Role Required"),
			exc=RoleGateError,
		)


def check_vendor_view_role(user: str | None = None) -> None:
	"""Raise if user doesn't have read access to Vendor Info Records."""
	if not _has_configured_role(
		"vendor_view_roles",
		("CH Vendor Manager", "CH Master Manager", "CH Master Approver", "CH Viewer", "System Manager"),
		user,
	):
		frappe.throw(
			_("You are not permitted to view Vendor Info Records."),
			title=_("Vendor Info Access Denied"),
			exc=RoleGateError,
		)


def check_gtin_editor_role(user: str | None = None) -> None:
	"""Raise if user doesn't have the CH GTIN Editor (or higher) role."""
	if not _has_configured_role(
		"gtin_editor_roles",
		("CH GTIN Editor", "CH Master Manager", "CH Master Approver", "System Manager"),
		user,
	):
		frappe.throw(
			_("CH GTIN Editor role is required to set or update a GTIN/EAN/UPC code."),
			title=_("GTIN Editor Role Required"),
			exc=RoleGateError,
		)


def check_mrp_planner_role(user: str | None = None) -> None:
	"""Raise if user doesn't have the CH MRP Planner (or higher) role."""
	if not _has_configured_role(
		"mrp_planner_roles",
		("CH MRP Planner", "CH Master Manager", "System Manager"),
		user,
	):
		frappe.throw(
			_("CH MRP Planner role is required to modify MRP/coverage planning fields."),
			title=_("MRP Planner Role Required"),
			exc=RoleGateError,
		)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Segregation of Duties
# ─────────────────────────────────────────────────────────────────────────────

def check_sod(submitted_by: str, approver: str | None = None) -> None:
	"""
	Enforce maker-checker: the user who submitted an item for approval
	cannot also be the one to approve it.

	Configured break-glass supervisors and Administrator are exempt.
	This matches Oracle EBS behavior where DBA/sysadmin profiles bypass SoD.
	Break-glass logging should be used when sysadmin approves own items.

	Raises SoDError if submitter == approver (and neither is a sysadmin).
	"""
	if not submitted_by:
		return  # no submitter recorded, skip check
	approver = approver or frappe.session.user
	if approver != submitted_by:
		return  # different users — no violation
	if _has_configured_role(
		"break_glass_supervisor_roles", ("System Manager",), approver
	):
		return
	frappe.throw(
		_("Segregation of Duties violation: <b>{0}</b> submitted this item for "
		  "review and cannot also approve it. A different approver is required.").format(submitted_by),
		title=_("SoD Violation"),
		exc=SoDError,
	)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Approval Delegation
# ─────────────────────────────────────────────────────────────────────────────

def is_effective_approver(user: str | None = None) -> bool:
	"""
	Return True if the user has effective approval authority — either via
	direct CH Master Approver role or through a valid, active CH Approval Delegation
	from a user who has the CH Master Approver role.
	
	SECURITY (H11): When using delegation, ensure delegator ≠ submitter
	(prevent self-delegation bypass of Segregation of Duties).
	"""
	user = user or frappe.session.user
	approver_defaults = ("CH Master Approver", "System Manager")

	# Direct role check
	if _has_configured_role("master_approval_roles", approver_defaults, user):
		return True

	# Delegation check
	today_date = today()
	delegations = frappe.get_all(
		"CH Approval Delegation",
		filters={"delegate": user, "active": 1},
		fields=["delegator", "valid_from", "valid_to"],
	)
	for d in delegations:
		# Check date bounds
		if d.valid_to and getdate(d.valid_to) < getdate(today_date):
			continue  # expired
		if d.valid_from and getdate(d.valid_from) > getdate(today_date):
			continue  # not yet started
		# SECURITY (H11): Ensure delegator ≠ delegate to prevent self-approval
		if d.delegator == user:
			continue  # delegator cannot delegate to themselves
		# Verify delegator actually has approver authority
		if _has_configured_role("master_approval_roles", approver_defaults, d.delegator):
			return True

	return False


# ─────────────────────────────────────────────────────────────────────────────
# 5. Custom DocPerm Installation (field-level security)
# ─────────────────────────────────────────────────────────────────────────────

def install_custom_docperms() -> dict:
	"""
	Create Custom DocPerm records at permlevel=1 for Item for configured roles.
	Idempotent — skips existing records.
	"""
	created = 0
	roles = get_role_setting(
		"sensitive_field_roles",
		("CH Price Manager", "CH Master Approver", "CH Master Manager", "CH GTIN Editor"),
	)
	for role in roles:
		if not frappe.db.exists("Role", role):
			continue  # role not yet installed, skip
		exists = frappe.db.exists(
			"Custom DocPerm",
			{"parent": "Item", "role": role, "permlevel": 1},
		)
		if exists:
			continue
		try:
			doc = frappe.get_doc({
				"doctype": "Custom DocPerm",
				"parent": "Item",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 1,
				"read": 1,
				"write": 1,
			})
			doc.insert(ignore_permissions=True)
			created += 1
		except Exception:
			frappe.log_error(
				title=f"install_custom_docperms: failed for role {role}",
				message=frappe.get_traceback(),
			)
	if created:
		frappe.clear_cache(doctype="Item")
	return {"custom_docperms_created": created}


# ─────────────────────────────────────────────────────────────────────────────
# 6. Time-Bound Role Expiry (daily scheduled task)
# ─────────────────────────────────────────────────────────────────────────────

def expire_role_assignments() -> dict:
	"""
	Expire a bounded batch of role assignments and revoke the corresponding roles.

	A user's roles are saved at most once per batch. Assignment statuses are updated
	in one statement only after the associated role removal succeeds. Failures are
	rolled back to a per-user savepoint, leaving those assignments active so the
	next scheduler run can retry them. The scheduler transaction is committed by
	Frappe after this function returns.
	"""
	if not frappe.db.table_exists("CH Role Assignment"):
		return {"expired": 0, "failed": 0, "has_more": False}

	today_date = today()
	batch_limit = min(get_int_setting("role_expiry_batch_limit", 500, minimum=1), 5000)
	expired_records = frappe.db.sql(
		"""
			SELECT `name`, `user`, `role`
			FROM `tabCH Role Assignment`
			WHERE `status` = 'Active'
			  AND `valid_to` < %(today)s
			ORDER BY `valid_to` ASC, `name` ASC
			LIMIT %(batch_limit)s
			FOR UPDATE
		""",
		{"today": today_date, "batch_limit": batch_limit},
		as_dict=True,
	)

	if not expired_records:
		return {"expired": 0, "failed": 0, "has_more": False}

	assignments_by_user: dict[str, list] = {}
	for record in expired_records:
		assignments_by_user.setdefault(record.user, []).append(record)

	users = tuple(assignments_by_user)
	roles = tuple(dict.fromkeys(record.role for record in expired_records))
	current_assignments = {
		(row.user, row.role)
		for row in frappe.db.sql(
			"""
				SELECT DISTINCT `user`, `role`
				FROM `tabCH Role Assignment`
				WHERE `status` = 'Active'
				  AND `valid_from` <= %(today)s
				  AND `valid_to` >= %(today)s
				  AND `user` IN %(users)s
				  AND `role` IN %(roles)s
			""",
			{"today": today_date, "users": users, "roles": roles},
			as_dict=True,
		)
	}

	successful_names: list[str] = []
	failed = 0
	for index, (user, records) in enumerate(assignments_by_user.items()):
		save_point = f"role_expiry_{index}"
		frappe.db.savepoint(save_point)
		try:
			roles_to_remove = {
				record.role
				for record in records
				if (user, record.role) not in current_assignments
			}
			if roles_to_remove:
				user_doc = frappe.get_doc("User", user)
				retained_roles = [row for row in (user_doc.roles or []) if row.role not in roles_to_remove]
				if len(retained_roles) != len(user_doc.roles or []):
					user_doc.roles = retained_roles
					user_doc.save(ignore_permissions=True)
			successful_names.extend(record.name for record in records)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			failed += len(records)
			frappe.log_error(
				title=f"Role expiry: failed to process assignments for {user}",
				message=frappe.get_traceback(),
			)

	if successful_names:
		placeholders = ", ".join(["%s"] * len(successful_names))
		frappe.db.sql(
			f"""
				UPDATE `tabCH Role Assignment`
				SET `status` = 'Expired', `modified` = %s, `modified_by` = %s
				WHERE `status` = 'Active'
				  AND `valid_to` < %s
				  AND `name` IN ({placeholders})
			""",
			(
				now_datetime(),
				frappe.session.user,
				today_date,
				*successful_names,
			),
		)

	return {
		"expired": len(successful_names),
		"failed": failed,
		"has_more": len(expired_records) == batch_limit,
	}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Break-Glass Emergency Access
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def open_break_glass(reason: str) -> str:
	"""
	Open a break-glass emergency access session.
	Creates a CH Break Glass Log entry and notifies configured supervisors.
	Returns the log document name.
	"""
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(
			_("A justification reason is required to open a Break Glass session."),
			title=_("Break Glass Reason Required"),
			exc=frappe.ValidationError,
		)

	doc = frappe.get_doc({
		"doctype": "CH Break Glass Log",
		"user": frappe.session.user,
		"reason": reason,
		"start_time": now_datetime(),
		"review_status": "Pending Review",
	})
	doc.insert()

	# Notify system managers (best-effort, no failure if email not configured)
	_notify_break_glass(doc.name, reason)

	return doc.name


@frappe.whitelist(methods=["POST"])
def close_break_glass(log_name: str, actions_taken: str = "") -> None:
	"""Close a break-glass session by setting end_time and actions_taken."""
	doc = frappe.get_doc("CH Break Glass Log", log_name)
	if doc.user != frappe.session.user and not _has_configured_role(
		"break_glass_supervisor_roles", ("System Manager",)
	):
		frappe.throw(_("You can only close your own Break Glass sessions."))
	doc.check_permission("write")
	doc.end_time = now_datetime()
	if actions_taken:
		doc.actions_taken = actions_taken
	doc.save()


def _notify_break_glass(log_name: str, reason: str) -> None:
	"""Send an alert to configured supervisors."""
	try:
		mgr_emails = _configured_role_users(
			"break_glass_supervisor_roles", ("System Manager",)
		)
		mgr_emails = [e for e in mgr_emails if "@" in (e or "")]
		if not mgr_emails:
			return
		frappe.sendmail(
			recipients=mgr_emails,
			subject=f"[SECURITY ALERT] Break Glass opened by {frappe.session.user}",
			message=(
				f"User <b>{frappe.session.user}</b> opened a Break Glass emergency session.<br>"
				f"<b>Reason:</b> {reason}<br>"
				f"<b>Log:</b> {log_name}<br>"
				f"Please review at your earliest opportunity."
			),
			now=False,
		)
	except Exception:
		frappe.log_error(title="Break Glass notification failed", message=frappe.get_traceback())


# ─────────────────────────────────────────────────────────────────────────────
# Break-Glass session monitoring (SAP GRC / Oracle Fusion Firefighter parity)
# Hourly scheduled task: escalates sessions that stay open past the SLA window.
# ─────────────────────────────────────────────────────────────────────────────

def monitor_break_glass_sessions() -> dict:
	"""Hourly scheduler hook.

	1. Marks sessions open beyond the configured SLA as ``Escalated``.
	2. Sends a consolidated digest e-mail to configured supervisors listing every
	   currently-open session (with hours-open) and every closed session that
	   is still ``Pending Review`` — so security can act in one place.

	Idempotent: only flips ``review_status`` when it is not already
	``Escalated``; the digest is sent only when there is something to report.
	"""
	from frappe.utils import get_datetime

	sla_hours = get_int_setting("break_glass_sla_hours", 4, minimum=1)
	hard_limit_hours = get_int_setting("break_glass_hard_limit_hours", 24, minimum=1)
	batch_limit = min(get_int_setting("break_glass_monitor_batch_limit", 200, minimum=1), 2000)
	now = now_datetime()

	open_rows = frappe.get_all(
		"CH Break Glass Log",
		filters={"end_time": ["is", "not set"]},
		fields=["name", "user", "reason", "start_time", "review_status"],
		order_by="start_time asc",
		limit=batch_limit,
	)

	overdue: list[dict] = []
	hard_breach: list[dict] = []
	for row in open_rows:
		start = get_datetime(row.start_time) if row.start_time else None
		if not start:
			continue
		hours = (now - start).total_seconds() / 3600
		row["hours_open"] = round(hours, 2)
		if hours > hard_limit_hours:
			hard_breach.append(row)
		elif hours > sla_hours:
			overdue.append(row)

	escalation_names = [
		row["name"]
		for row in overdue + hard_breach
		if row.get("review_status") != "Escalated"
	]
	if escalation_names:
		placeholders = ", ".join(["%s"] * len(escalation_names))
		frappe.db.sql(
			f"""
				UPDATE `tabCH Break Glass Log`
				SET `review_status` = 'Escalated'
				WHERE `name` IN ({placeholders})
				  AND `review_status` != 'Escalated'
			""",
			tuple(escalation_names),
		)

	pending_review = frappe.get_all(
		"CH Break Glass Log",
		filters={"end_time": ["is", "set"], "review_status": "Pending Review"},
		fields=["name", "user", "start_time", "end_time", "duration_hours"],
		order_by="end_time asc",
		limit=20,
	)

	if not (overdue or hard_breach or pending_review):
		return {
			"open_overdue": 0,
			"open_hard_breach": 0,
			"pending_review": 0,
			"has_more": len(open_rows) == batch_limit,
		}

	_send_break_glass_digest(overdue, hard_breach, pending_review, sla_hours, hard_limit_hours)
	return {
		"open_overdue": len(overdue),
		"open_hard_breach": len(hard_breach),
		"pending_review": len(pending_review),
		"has_more": len(open_rows) == batch_limit,
	}


def _send_break_glass_digest(
	overdue: list[dict],
	hard_breach: list[dict],
	pending_review: list[dict],
	sla_hours: int,
	hard_limit_hours: int,
) -> None:
	"""Email a consolidated security digest to configured supervisors."""
	try:
		recipients = _configured_role_users(
			"break_glass_supervisor_roles", ("System Manager",)
		)
		recipients = [e for e in recipients if "@" in (e or "")]
		if not recipients:
			return

		def _table(title: str, rows: list[dict], cols: list[tuple[str, str]]) -> str:
			if not rows:
				return ""
			header = "".join(f"<th align='left' style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{label}</th>" for _, label in cols)
			body = "".join(
				"<tr>" + "".join(
					f"<td style='padding:6px 10px;border-bottom:1px solid #f1f5f9'>{frappe.utils.escape_html(str(r.get(key) or '-'))}</td>"
					for key, _ in cols
				) + "</tr>"
				for r in rows
			)
			return (
				f"<h3 style='margin:18px 0 6px;color:#0f172a'>{title}</h3>"
				f"<table style='border-collapse:collapse;width:100%;font-size:13px'>"
				f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
			)

		open_cols = [
			("name", "Log"), ("user", "User"),
			("hours_open", "Hours Open"), ("start_time", "Started"),
		]
		review_cols = [
			("name", "Log"), ("user", "User"),
			("duration_hours", "Duration (h)"), ("end_time", "Closed"),
		]

		html = (
			"<p>Hourly Break Glass governance digest. Review and close the "
			"items below in the <b>CH Break Glass Audit</b> report.</p>"
			+ _table(f"Hard SLA Breach (>{hard_limit_hours}h open)", hard_breach, open_cols)
			+ _table(f"SLA Breach (>{sla_hours}h open)", overdue, open_cols)
			+ _table("Closed sessions pending review", pending_review, review_cols)
		)

		frappe.sendmail(
			recipients=recipients,
			subject="[SECURITY DIGEST] Break Glass — open & pending-review sessions",
			message=html,
			now=False,
		)
	except Exception:
		frappe.log_error(
			title="Break Glass digest failed",
			message=frappe.get_traceback(),
		)
