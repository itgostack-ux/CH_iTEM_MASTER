# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Company + category routed approval for price change batches.

A price batch usually spans several product categories. Market-standard ERPs
do not ask one person to approve the whole document: SAP splits a release
strategy across release codes, Oracle AME builds a per-dimension approver
list, and Odoo fans an approval request out to category owners. This module
does the same for ``CH Price Upload Batch``.

Routing
-------
Each price row carries its item's ``CH Category``. On submit the batch is
split into one approval row per distinct category, and each row is routed to:

1. ``CH Category.category_manager`` — the category's owner; else
2. ``Company.ch_company_head`` — the company-level fallback.

A category is never left unrouted, and nothing is ever auto-approved.
``CH Approval Delegation`` is honoured so an out-of-office owner forwards to
their delegate — the same substitution the exception framework applies.

Decisions are per category: an approver sees and actions only their own rows.
Approved categories apply immediately (SAP partial release) rather than
waiting on the slowest approver; rejected categories are dropped; pending
categories stay queued.

This mirrors ``CH Free Sale Approval`` in ch_pos, which already fans a POS
cart out to its category managers, and reuses the notification scoping from
``ch_erp15.notification_router``.
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from ch_item_master.config import get_int_setting, has_role_setting
from ch_item_master.security import get_company_filter_value, require_scoped_document_action

CATEGORY_MANAGER = "Category Manager"
COMPANY_HEAD = "Company Head"


# ─────────────────────────────────────────────────────────────────────────────
# Approver resolution
# ─────────────────────────────────────────────────────────────────────────────

def _apply_delegation(user):
	"""Forward to an active delegate if the approver is out of office.

	Mirrors ``exception_api._apply_delegation`` so both approval surfaces
	honour the same ``CH Approval Delegation`` records.
	"""
	if not user:
		return user
	try:
		from frappe.utils import getdate
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
	except Exception:
		# Delegation is a convenience, never a gate — a broken delegation
		# record must not stop a batch from being routed.
		frappe.log_error(frappe.get_traceback(), "Price approval delegation lookup failed")
	return user


def _enabled(user):
	return bool(user) and bool(frappe.db.get_value("User", user, "enabled"))


def resolve_category_approver(category, company):
	"""Return ``(approver, routed_via)`` for one category in one company.

	Falls back to the company head when the category has no manager mapped,
	so a batch is never blocked by incomplete master data.
	"""
	manager = None
	if category:
		manager = frappe.db.get_value("CH Category", category, "category_manager")
	if _enabled(manager):
		return _apply_delegation(manager), CATEGORY_MANAGER

	head = None
	if company:
		head = frappe.db.get_value("Company", company, "ch_company_head")
	if _enabled(head):
		return _apply_delegation(head), COMPANY_HEAD

	return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Split
# ─────────────────────────────────────────────────────────────────────────────

def _row_value(row):
	"""Numeric value of a price row, for the category's total.

	``new_value`` is a Data field carrying tag names as well as prices, so a
	non-numeric value contributes nothing rather than raising.
	"""
	try:
		return flt(row.new_value)
	except Exception:
		return 0.0


def stamp_row_categories(batch):
	"""Denormalise each row's category from its Item.

	Stored on the row rather than joined at approval time so that a later
	re-categorisation of an item cannot silently move rows between approvers
	mid-approval.
	"""
	codes = {r.item_code for r in batch.items if r.item_code}
	if not codes:
		return
	cats = dict(
		frappe.get_all(
			"Item",
			filters={"name": ("in", list(codes))},
			fields=["name", "ch_category"],
			as_list=True,
		)
	)
	for row in batch.items:
		row.category = cats.get(row.item_code) or None


def build_category_approvals(batch):
	"""Populate ``category_approvals``, one row per distinct category.

	Returns the list of categories that could not be routed, so the caller
	can fail loudly rather than creating an approval nobody owns.
	"""
	stamp_row_categories(batch)

	buckets = {}
	for row in batch.items:
		key = row.category or ""
		b = buckets.setdefault(key, {"rows": 0, "value": 0.0})
		b["rows"] += 1
		b["value"] += _row_value(row)

	batch.set("category_approvals", [])
	unrouted = []
	for category in sorted(buckets):
		approver, routed_via = resolve_category_approver(category, batch.company)
		if not approver:
			unrouted.append(category or _("(no category)"))
			continue
		batch.append("category_approvals", {
			"category": category or None,
			"approver": approver,
			"routed_via": routed_via,
			"status": "Pending",
			"row_count": buckets[category]["rows"],
			"total_value": buckets[category]["value"],
		})
	return unrouted


# ─────────────────────────────────────────────────────────────────────────────
# Notification + inbox
# ─────────────────────────────────────────────────────────────────────────────

def _batch_url(batch):
	return frappe.utils.get_url(f"/app/ch-price-upload-batch/{batch.name}")


def _notify(recipients, subject, message, batch):
	"""In-desk notification plus email, best-effort.

	Uses ch_erp15's sender so approvers get the desk bell as well as mail;
	CH Exception Request only sends mail, which is why its alerts are easy to
	miss. Never allowed to break the approval flow.
	"""
	if not recipients:
		return
	try:
		from ch_erp15.ch_erp15.store_request_api import _send_notification
		_send_notification(
			recipients, subject, message,
			reference_doctype="CH Price Upload Batch",
			reference_name=batch.name,
			email=True,
		)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Price approval notification failed for {batch.name}",
		)


def _assign(batch, user, category):
	"""Put the batch in the approver's ToDo inbox.

	Goes through the assignment API rather than a raw ToDo insert so ``_assign``
	is maintained on the parent and the standard Frappe assignment UI works.
	"""
	if not user:
		return
	try:
		existing = frappe.get_all(
			"ToDo",
			filters={
				"reference_type": "CH Price Upload Batch",
				"reference_name": batch.name,
				"allocated_to": user,
				"status": "Open",
			},
			limit=1,
		)
		if existing:
			return
		from frappe.desk.form.assign_to import add as assign_add
		assign_add({
			"assign_to": [user],
			"doctype": "CH Price Upload Batch",
			"name": batch.name,
			"description": _("Approve price changes for category {0}").format(category or "-"),
			"priority": "Medium",
		})
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Price approval assignment failed for {batch.name}",
		)


def _close_assignment(batch, user):
	for todo in frappe.get_all(
		"ToDo",
		filters={
			"reference_type": "CH Price Upload Batch",
			"reference_name": batch.name,
			"allocated_to": user,
			"status": "Open",
		},
		pluck="name",
	):
		try:
			frappe.db.set_value("ToDo", todo, "status", "Closed")
		except Exception:
			pass


def notify_approvers(batch):
	"""Alert every pending category approver, once each."""
	seen = set()
	for row in batch.category_approvals:
		if row.status != "Pending" or not row.approver or row.approver in seen:
			continue
		seen.add(row.approver)
		mine = [
			r for r in batch.category_approvals
			if r.approver == row.approver and r.status == "Pending"
		]
		cats = ", ".join(frappe.bold(r.category or "-") for r in mine)
		rows = sum(int(r.row_count or 0) for r in mine)
		subject = _("Price approval needed: {0}").format(batch.name)
		message = _(
			"<p>{submitter} submitted price changes needing your approval.</p>"
			"<p><b>Batch:</b> {batch}<br>"
			"<b>Company:</b> {company}<br>"
			"<b>Your categories:</b> {cats}<br>"
			"<b>Rows awaiting you:</b> {rows}</p>"
			"<p><a href='{url}'>Review and approve</a></p>"
		).format(
			submitter=batch.submitted_by or batch.uploaded_by or "-",
			batch=batch.name,
			company=batch.company or "-",
			cats=cats,
			rows=rows,
			url=_batch_url(batch),
		)
		_notify([row.approver], subject, message, batch)
		_assign(batch, row.approver, mine[0].category)


def _notify_submitter(batch, category, action, actor, reason=None):
	submitter = batch.submitted_by or batch.uploaded_by
	if not submitter or submitter == actor:
		return
	subject = _("Price changes {0} for {1}").format(action.lower(), category or "-")
	message = _(
		"<p><b>{actor}</b> {action} the <b>{cat}</b> price changes in batch "
		"<b>{batch}</b>.</p>"
	).format(actor=actor, action=action.lower(), cat=category or "-", batch=batch.name)
	if reason:
		message += _("<p><b>Reason:</b> {0}</p>").format(reason)
	message += f"<p><a href='{_batch_url(batch)}'>Open batch</a></p>"
	_notify([submitter], subject, message, batch)


# ─────────────────────────────────────────────────────────────────────────────
# Decisions
# ─────────────────────────────────────────────────────────────────────────────

def _is_override(user=None):
	return has_role_setting(
		"price_batch_override_roles",
		("System Manager", "CH Master Manager"),
		user=user,
	)


def assert_can_action(batch, row, user=None):
	"""Guard a per-category decision.

	Three independent gates, all server-side — the previous implementation
	had only a client-side role check, so any user who could read the doc
	could approve it over the REST API.
	"""
	user = user or frappe.session.user

	if row.approver != user and not _is_override(user):
		frappe.throw(
			_("Category {0} is routed to {1}. You cannot action it.").format(
				frappe.bold(row.category or "-"), frappe.bold(row.approver)
			),
			frappe.PermissionError,
			title=_("Not Your Category"),
		)

	if not (
		has_role_setting(
			"price_batch_approval_roles",
			("CH Category Head", "CH Price Manager", "CH Master Manager"),
			user=user,
		)
		or _is_override(user)
	):
		frappe.throw(
			_("You do not hold a role permitted to approve price changes."),
			frappe.PermissionError,
			title=_("Not Permitted"),
		)

	# Segregation of duties — the submitter cannot approve their own batch.
	from ch_item_master.ch_item_master.rbac import check_sod
	check_sod(batch.submitted_by, user)


def _roll_up(batch):
	"""Derive the parent status from the category rows.

	Any rejection alone does not reject the batch — other categories may still
	be approved and applied. The batch is Rejected only when nothing survived.
	"""
	rows = batch.category_approvals or []
	if not rows:
		return
	statuses = [r.status for r in rows]
	pending = statuses.count("Pending")
	approved = statuses.count("Approved")

	if pending:
		batch.status = "Partially Approved" if approved else "Pending Approval"
	elif approved:
		batch.status = "Approved"
	else:
		batch.status = "Rejected"
		batch.rejected_by = frappe.session.user
		batch.rejected_at = now_datetime()


def _sync_row_approval(batch, category, status):
	for row in batch.items:
		if (row.category or "") == (category or ""):
			row.approval_status = status


@frappe.whitelist(methods=["POST"])
def decide_category(batch_name, category, action, comments=None):
	"""Approve or reject one category's rows within a batch.

	Approved categories are applied immediately; the rest of the batch keeps
	waiting on its own approvers.
	"""
	if action not in ("Approve", "Reject"):
		frappe.throw(_("Unknown action {0}.").format(action))
	if action == "Reject" and not (comments or "").strip():
		frappe.throw(_("A reason is required to reject."), title=_("Reason Required"))

	batch = frappe.get_doc("CH Price Upload Batch", batch_name)
	require_scoped_document_action(
		batch,
		None,
		action=_("decide a price upload category"),
		permission_types=("write",),
		lock=True,
	)
	if batch.status not in ("Pending Approval", "Partially Approved"):
		frappe.throw(
			_("Batch is {0} — only batches awaiting approval can be actioned.").format(batch.status),
			title=_("Not Awaiting Approval"),
		)

	category = category or ""
	row = next(
		(r for r in batch.category_approvals if (r.category or "") == category), None
	)
	if not row:
		frappe.throw(_("Category {0} is not part of this batch.").format(category or "-"))
	if row.status != "Pending":
		frappe.throw(
			_("Category {0} was already {1}.").format(category or "-", row.status.lower()),
			title=_("Already Decided"),
		)

	assert_can_action(batch, row)

	decided = "Approved" if action == "Approve" else "Rejected"
	row.status = decided
	row.responded_at = now_datetime()
	row.comments = (comments or "").strip() or None
	_sync_row_approval(batch, category, decided)

	if decided == "Rejected":
		note = _("{0}: {1}").format(category or "-", (comments or "").strip())
		batch.rejection_reason = (
			f"{batch.rejection_reason}\n{note}" if batch.rejection_reason else note
		)

	_roll_up(batch)

	if decided == "Approved":
		batch.approved_by = frappe.session.user
		batch.approved_at = now_datetime()

	batch._authorize_approval_transition()
	batch.save()

	# Close this approver's inbox item once they have nothing left pending.
	if not any(
		r.status == "Pending" and r.approver == frappe.session.user
		for r in batch.category_approvals
	):
		_close_assignment(batch, frappe.session.user)

	applied = None
	if decided == "Approved":
		applied = batch.apply_approved_categories()

	_notify_submitter(batch, category, decided, frappe.session.user, comments)
	return {
		"batch": batch.name,
		"category": category,
		"decision": decided,
		"batch_status": batch.status,
		"applied": applied,
	}


@frappe.whitelist()
def get_my_pending_approvals(company=None):
	"""Price categories awaiting the current user.

	Backs the approver's queue; shaped like the Material Request category
	inbox so both read the same way.
	"""
	user = frappe.session.user
	override = _is_override(user)
	if not override and not has_role_setting(
		"price_batch_approval_roles",
		("CH Category Head", "CH Price Manager", "CH Master Manager"),
		user=user,
	):
		frappe.throw(
			_("You do not hold a role permitted to view price approvals."),
			frappe.PermissionError,
		)

	frappe.has_permission("CH Price Upload Batch", "read", throw=True)
	batch_filters = {
		"status": ("in", ("Pending Approval", "Partially Approved")),
	}
	company_filter = get_company_filter_value(company, user=user)
	if company_filter is not None:
		batch_filters["company"] = company_filter

	queue_limit = min(get_int_setting("price_approval_queue_limit", 100, minimum=1), 500)
	batches = frappe.get_list(
		"CH Price Upload Batch",
		filters=batch_filters,
		fields=["name", "title", "company", "submitted_by", "submitted_at"],
		order_by="submitted_at asc, name asc",
		limit_page_length=queue_limit,
	)
	if not batches:
		return []

	batch_by_name = {batch.name: batch for batch in batches}
	approval_filters = {
		"parent": ("in", tuple(batch_by_name)),
		"parenttype": "CH Price Upload Batch",
		"status": "Pending",
	}
	if not override:
		approval_filters["approver"] = user
	approvals = frappe.get_all(
		"CH Price Approval Category",
		filters=approval_filters,
		fields=[
			"parent", "category", "approver", "routed_via", "row_count", "total_value",
		],
		order_by="parent asc, idx asc",
		limit_page_length=queue_limit,
	)

	result = []
	for approval in approvals:
		batch = batch_by_name.get(approval.parent)
		if not batch:
			continue
		result.append(frappe._dict({
			"batch": batch.name,
			"title": batch.title,
			"company": batch.company,
			"submitted_by": batch.submitted_by,
			"submitted_at": batch.submitted_at,
			"category": approval.category,
			"approver": approval.approver,
			"routed_via": approval.routed_via,
			"row_count": approval.row_count,
			"total_value": approval.total_value,
		}))
	return result
