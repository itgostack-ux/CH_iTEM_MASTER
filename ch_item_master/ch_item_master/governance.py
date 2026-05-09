# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Item Master Governance Module

Centralises:
  - Lifecycle state machine (Draft -> Pending Review -> Active -> Obsolete/Blocked)
  - Transaction-level guard (block non-Active items)
  - Soft duplicate detection (normalised signature match)
  - Completeness profile validation per item_nature
  - Audit log writer (append-only)
  - Workflow installer (Frappe Workflow doctype)

Wired from hooks.py (Item before_insert/before_save) and from
sales/purchase/stock document validate hooks.
"""

from __future__ import annotations

import re
from typing import Iterable

import frappe
from frappe import _
from frappe.utils import now_datetime

from ch_item_master.ch_item_master.exceptions import (
	IncompleteItemMasterError,
	InvalidLifecycleTransitionError,
	ItemNotActiveError,
	SoftDuplicateError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

LIFECYCLE_STATES = ("Draft", "Pending Review", "Active", "Obsolete", "Blocked")
ACTIVE_STATE = "Active"
NON_TRANSACTABLE_STATES = ("Draft", "Pending Review", "Obsolete", "Blocked")

# Allowed forward transitions; any other movement requires CH Master Approver
# role OR System Manager. "Anyone" = creator/editor; "Approver" = approver-only.
_ALLOWED_TRANSITIONS = {
	"Draft":          {"Pending Review": "Anyone", "Active": "Approver"},
	"Pending Review": {"Active": "Approver", "Draft": "Anyone", "Blocked": "Approver"},
	"Active":         {"Obsolete": "Approver", "Blocked": "Approver"},
	"Obsolete":       {"Active": "Approver"},
	"Blocked":        {"Active": "Approver"},
}

_APPROVER_ROLES = {"CH Master Approver", "System Manager", "Administrator"}

# Audit-tracked fields on Item — these record an audit entry on change.
_AUDITED_ITEM_FIELDS = (
	"ch_lifecycle_status",
	"is_stock_item",
	"gst_hsn_code",
	"stock_uom",
	"valuation_method",
	"disabled",
	"has_serial_no",
	"has_batch_no",
)


# ─────────────────────────────────────────────────────────────────────────────
# Utility: role + normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _user_roles(user: str | None = None) -> set[str]:
	user = user or frappe.session.user
	return set(frappe.get_roles(user))


def _is_approver() -> bool:
	return bool(_user_roles() & _APPROVER_ROLES)


_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm_signature(*parts: Iterable[str]) -> str:
	"""Normalise a tuple of strings into a stable comparison signature."""
	flat = " ".join((p or "") for p in parts).lower()
	return _NORM_RE.sub("-", flat).strip("-")


# ─────────────────────────────────────────────────────────────────────────────
# Audit log writer
# ─────────────────────────────────────────────────────────────────────────────

def write_audit(
	item: str,
	action: str,
	field_name: str = "",
	old_value=None,
	new_value=None,
	remarks: str = "",
	trace_id: str = "",
) -> None:
	"""Append a CH Item Audit Log row. Never raises — audit must not break
	the user's transaction. Errors are logged."""
	try:
		doc = frappe.new_doc("CH Item Audit Log")
		doc.item = item
		doc.action = action
		doc.field_name = field_name or ""
		doc.changed_by = frappe.session.user
		doc.changed_on = now_datetime()
		doc.old_value = "" if old_value is None else str(old_value)
		doc.new_value = "" if new_value is None else str(new_value)
		doc.remarks = remarks or ""
		doc.trace_id = trace_id or ""
		doc.flags.ignore_permissions = True
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title="CH Item Audit Log write failed",
			message=frappe.get_traceback(),
		)


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle: validate transitions on Item
# ─────────────────────────────────────────────────────────────────────────────

def validate_lifecycle_transition(doc) -> None:
	"""Block invalid lifecycle transitions on Item.

	- New items always start in Draft (forced).
	- State machine enforced by _ALLOWED_TRANSITIONS.
	- Transitions tagged 'Approver' require approver role.
	"""
	new_status = (doc.get("ch_lifecycle_status") or "").strip()

	if doc.is_new():
		if not new_status:
			doc.ch_lifecycle_status = "Draft"
			return
		# Allow inserting straight into Active only if approver (e.g. data import).
		if new_status != "Draft" and not _is_approver():
			raise InvalidLifecycleTransitionError(
				_("Items can only be created in 'Draft' status. Approver role is required to start in '{0}'.").format(new_status)
			)
		return

	if not doc.has_value_changed("ch_lifecycle_status"):
		return

	before = doc.get_doc_before_save()
	old_status = (before.get("ch_lifecycle_status") if before else "") or "Draft"

	if old_status == new_status:
		return

	allowed = _ALLOWED_TRANSITIONS.get(old_status, {})
	if new_status not in allowed:
		raise InvalidLifecycleTransitionError(
			_("Invalid lifecycle transition: '{0}' → '{1}' is not allowed.").format(old_status, new_status)
		)

	if allowed[new_status] == "Approver" and not _is_approver():
		raise InvalidLifecycleTransitionError(
			_("Transition '{0}' → '{1}' requires the 'CH Master Approver' role.").format(old_status, new_status)
		)


# ─────────────────────────────────────────────────────────────────────────────
# Transaction guard
# ─────────────────────────────────────────────────────────────────────────────

_TRANSACTION_ITEM_FIELD = "item_code"


def assert_item_transactable(item_code: str, doctype: str = "") -> None:
	"""Raise ItemNotActiveError if the Item is not in 'Active' lifecycle state.

	Cheap single-field DB lookup; no doc load.
	"""
	if not item_code:
		return
	status = frappe.db.get_value("Item", item_code, "ch_lifecycle_status")
	# Backward-compat: items that pre-date the field show as None — treat as
	# Active so existing data keeps working until the backfill patch runs.
	if status and status != ACTIVE_STATE:
		raise ItemNotActiveError(
			_("Item {0} is in lifecycle status '{1}' and cannot be used in {2}. Activate the item first.").format(
				frappe.bold(item_code), status, doctype or _("transactions")
			)
		)


def validate_transaction_items(doc, method=None) -> None:
	"""Hook target: validate every line item on a transaction is Active.

	Wired on Sales Invoice / POS Invoice / Purchase Order / Stock Entry /
	Delivery Note / Purchase Receipt via hooks.py doc_events.
	"""
	if not getattr(doc, "items", None):
		return
	# Allow draft transactions to be saved with non-Active items? No — surface
	# the error early so users know to fix the master, but only enforce at
	# validate (save) time for transactional doctypes (already submit-gated).
	for row in doc.items:
		assert_item_transactable(getattr(row, _TRANSACTION_ITEM_FIELD, None), doctype=doc.doctype)


# ─────────────────────────────────────────────────────────────────────────────
# Soft duplicate detection
# ─────────────────────────────────────────────────────────────────────────────

def check_soft_duplicate(doc) -> None:
	"""Warn when an Item with the same (manufacturer + model + brand + name)
	signature already exists. Hard-blocks for Variant Templates; warns for
	other natures.
	"""
	if doc.variant_of:
		# Variants inherit signature from template — skip.
		return

	mfr = doc.get("default_item_manufacturer") or ""
	sig = _norm_signature(
		mfr,
		doc.get("ch_model") or "",
		doc.get("brand") or "",
		doc.get("item_name") or "",
	)
	if not sig or sig == "-":
		return

	# Find candidate items with the same key fields (cheap pre-filter on
	# ch_model when present) and compute signature in Python for accuracy.
	filters = {"name": ["!=", doc.name or ""]}
	if doc.get("ch_model"):
		filters["ch_model"] = doc.ch_model
	elif mfr:
		filters["default_item_manufacturer"] = mfr
	else:
		# Without any anchor, scope by item_group to cap cost.
		if not doc.get("item_group"):
			return
		filters["item_group"] = doc.item_group

	try:
		candidates = frappe.get_all(
			"Item",
			filters=filters,
			fields=["name", "item_name", "default_item_manufacturer", "ch_model", "brand"],
			limit=200,
		)
	except Exception:
		# Defensive: if a custom column was removed, skip dup check rather than
		# breaking item save. Audit-log the issue once.
		frappe.log_error(title="check_soft_duplicate query failed", message=frappe.get_traceback())
		return

	for c in candidates:
		c_sig = _norm_signature(
			c.get("default_item_manufacturer") or "",
			c.ch_model or "",
			c.brand or "",
			c.item_name or "",
		)
		if c_sig and c_sig == sig:
			subcat_nature = ""
			if doc.get("ch_sub_category"):
				subcat_nature = frappe.db.get_value(
					"CH Sub Category", doc.ch_sub_category, "item_nature"
				) or ""
			msg = _("Possible duplicate: Item {0} has the same Manufacturer/Model/Brand/Name signature.").format(
				frappe.bold(c.name)
			)
			if subcat_nature == "Variant Template":
				raise SoftDuplicateError(msg)
			frappe.msgprint(msg, indicator="orange", title=_("Soft Duplicate Detected"))
			return


# ─────────────────────────────────────────────────────────────────────────────
# Completeness profile per item_nature
# ─────────────────────────────────────────────────────────────────────────────

def validate_completeness(doc) -> None:
	"""Enforce per-nature mandatory fields when the Item is being moved into
	an Active lifecycle state (or is already Active and being saved)."""
	target_status = (doc.get("ch_lifecycle_status") or "").strip()
	if target_status not in ("Active",):
		return  # Only enforce at activation; Draft can be incomplete.

	missing: list[str] = []

	if not doc.get("gst_hsn_code"):
		missing.append("gst_hsn_code")
	if not doc.get("stock_uom"):
		missing.append("stock_uom")
	if not doc.get("ch_sub_category"):
		missing.append("ch_sub_category")
	if not doc.get("item_group"):
		missing.append("item_group")

	nature = ""
	if doc.get("ch_sub_category"):
		sc = frappe.db.get_value(
			"CH Sub Category", doc.ch_sub_category,
			["item_nature", "income_account", "expense_account",
			 "subscription_duration_months_default", "gofix_service_category"],
			as_dict=True,
		) or {}
		nature = (sc.get("item_nature") or "").strip()

		if nature == "Service" and not sc.get("gofix_service_category"):
			missing.append("sub_category.gofix_service_category")
		if nature == "Subscription" and not (sc.get("subscription_duration_months_default") or 0):
			missing.append("sub_category.subscription_duration_months_default")
		if nature in ("Service", "Subscription") and not sc.get("income_account"):
			missing.append("sub_category.income_account")

	if nature == "Asset / Capital" and not doc.get("has_serial_no"):
		missing.append("has_serial_no")

	if missing:
		raise IncompleteItemMasterError(
			_("Cannot activate Item {0}. Missing required fields for nature '{1}': {2}").format(
				frappe.bold(doc.name or doc.item_name or "<new>"),
				nature or _("<unspecified>"),
				", ".join(missing),
			)
		)


# ─────────────────────────────────────────────────────────────────────────────
# Item hook entry points
# ─────────────────────────────────────────────────────────────────────────────

def on_item_before_save(doc, method=None) -> None:
	"""Hook target — runs after ch_item_master.overrides.item.before_save.

	Order matters: lifecycle/dup/completeness check first, then audit. We
	let validation raise (rolls back save). Audit only fires for fields that
	actually changed AND validation passed.
	"""
	# Force default lifecycle for new items
	if doc.is_new() and not doc.get("ch_lifecycle_status"):
		doc.ch_lifecycle_status = "Draft"

	validate_lifecycle_transition(doc)
	check_soft_duplicate(doc)
	validate_completeness(doc)


def on_item_after_save(doc, method=None) -> None:
	"""Hook target on `on_update` — write audit entries for changed fields."""
	if doc.is_new():
		write_audit(doc.name, "Create", remarks=f"Item created in status {doc.get('ch_lifecycle_status') or 'Draft'}")
		return

	before = doc.get_doc_before_save()
	if not before:
		return

	for field in _AUDITED_ITEM_FIELDS:
		old = before.get(field)
		new = doc.get(field)
		if (old or "") == (new or ""):
			continue
		action = "Update"
		if field == "ch_lifecycle_status":
			action = {
				"Active": "Approve" if new == "Active" else "Lifecycle Change",
				"Blocked": "Block",
				"Obsolete": "Obsolete",
			}.get(new, "Lifecycle Change")
		elif field == "is_stock_item":
			action = "Stock Flag Change"
		write_audit(doc.name, action, field_name=field, old_value=old, new_value=new)


# ─────────────────────────────────────────────────────────────────────────────
# Workflow installer (Frappe Workflow doctype)
# ─────────────────────────────────────────────────────────────────────────────

_WORKFLOW_NAME = "Item Master Workflow"
_WORKFLOW_STATES = [
	{"state": "Draft",          "doc_status": 0, "style": "Warning",   "allow_edit": "CH Master Manager"},
	{"state": "Pending Review", "doc_status": 0, "style": "Primary",   "allow_edit": "CH Master Approver"},
	{"state": "Active",         "doc_status": 0, "style": "Success",   "allow_edit": "CH Master Approver"},
	{"state": "Obsolete",       "doc_status": 0, "style": "Inverse",   "allow_edit": "CH Master Approver"},
	{"state": "Blocked",        "doc_status": 0, "style": "Danger",    "allow_edit": "CH Master Approver"},
]
_WORKFLOW_TRANSITIONS = [
	{"state": "Draft",          "action": "Submit for Review", "next_state": "Pending Review", "allowed": "CH Master Manager"},
	{"state": "Pending Review", "action": "Approve",           "next_state": "Active",         "allowed": "CH Master Approver"},
	{"state": "Pending Review", "action": "Reject",            "next_state": "Draft",          "allowed": "CH Master Approver"},
	{"state": "Pending Review", "action": "Block",             "next_state": "Blocked",        "allowed": "CH Master Approver"},
	{"state": "Active",         "action": "Mark Obsolete",     "next_state": "Obsolete",       "allowed": "CH Master Approver"},
	{"state": "Active",         "action": "Block",             "next_state": "Blocked",        "allowed": "CH Master Approver"},
	{"state": "Obsolete",       "action": "Reactivate",        "next_state": "Active",         "allowed": "CH Master Approver"},
	{"state": "Blocked",        "action": "Reactivate",        "next_state": "Active",         "allowed": "CH Master Approver"},
]


def install_workflows() -> None:
	"""Idempotent installer — runs from after_migrate.

	Creates Workflow State, Workflow Action Master, and one Workflow doc
	bound to Item.ch_lifecycle_status.

	Note: Workflow is attached to Item ONLY. CH Sub Category and CH Category
	have the lifecycle_status field but no Workflow doctype — their state
	machine is enforced by application code paths (and is normally set
	directly by approvers via the form). Attaching Frappe Workflow to those
	masters would block fixture/data-load flows that legitimately set
	lifecycle_status='Active' on insert (backfill, imports, tests).
	"""
	# 1. Ensure Workflow State + Action Master records exist
	for s in _WORKFLOW_STATES:
		_ensure_state(s["state"], s["style"])
	for t in _WORKFLOW_TRANSITIONS:
		_ensure_action(t["action"])

	# 2. Item workflow only.
	_ensure_workflow(
		name=_WORKFLOW_NAME,
		document_type="Item",
		state_field="ch_lifecycle_status",
	)


def _ensure_state(state: str, style: str) -> None:
	if frappe.db.exists("Workflow State", state):
		return
	doc = frappe.new_doc("Workflow State")
	doc.workflow_state_name = state
	doc.style = style
	doc.insert(ignore_permissions=True)


def _ensure_action(action: str) -> None:
	if frappe.db.exists("Workflow Action Master", action):
		return
	doc = frappe.new_doc("Workflow Action Master")
	doc.workflow_action_name = action
	doc.insert(ignore_permissions=True)


def _ensure_workflow(name: str, document_type: str, state_field: str) -> None:
	# Skip silently if either the target doctype or the state_field column
	# isn't present yet (first migrate before custom_fields run).
	if not frappe.db.exists("DocType", document_type):
		return
	meta = frappe.get_meta(document_type)
	if not meta.get_field(state_field):
		return

	if frappe.db.exists("Workflow", name):
		wf = frappe.get_doc("Workflow", name)
	else:
		wf = frappe.new_doc("Workflow")
		wf.workflow_name = name

	wf.document_type = document_type
	wf.workflow_state_field = state_field
	# Install INACTIVE by default so this can be enabled by an admin without
	# silently breaking existing item save flows in installations that
	# previously created items without going through Draft -> Pending -> Active.
	wf.is_active = 0
	wf.send_email_alert = 0
	wf.states = []
	wf.transitions = []

	for s in _WORKFLOW_STATES:
		wf.append("states", {
			"state": s["state"],
			"doc_status": s["doc_status"],
			"allow_edit": s["allow_edit"],
		})
	for t in _WORKFLOW_TRANSITIONS:
		wf.append("transitions", {
			"state": t["state"],
			"action": t["action"],
			"next_state": t["next_state"],
			"allowed": t["allowed"],
		})

	wf.flags.ignore_permissions = True
	wf.flags.ignore_links = True
	wf.save(ignore_permissions=True)
