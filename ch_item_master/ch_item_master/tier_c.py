# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Item Master Tier C — Oracle-parity module.

Features:
  1. Item Revision / Version Control     — auto-snapshot Item fields on every save
  2. Formal Model Approval Gate          — Draft → Submitted → Approved → Rejected
                                           must be Approved before lifecycle = Active
  3. GTIN + Trading-Partner Aliases      — ch_gtin field + CH Item Trading Partner Alias rows
  4. MRP / Coverage Planning Fields      — ch_mrp_type, reorder_point, safety stock, lead days,
                                           lot_size + per-site defaults via CH Item Site Default
  5. Vendor Info-Record Sourcing Master  — standalone CH Vendor Info Record doctype + API
  6. Full PLM State Machine              — NPI → Under Review → Sample Testing → Approved
                                           → Active Production → End of Life → Discontinued
                                           with transaction-blocking rules per state

Wired from hooks.py doc_events.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import now_datetime, today, getdate, flt

from ch_item_master.ch_item_master.exceptions import ItemNotActiveError


# ─────────────────────────────────────────────────────────────────────────────
# 1. Item Revision / Version Control
# ─────────────────────────────────────────────────────────────────────────────

_SNAPSHOT_FIELDS = [
	"item_code", "item_name", "description", "item_group",
	"ch_category", "ch_sub_category", "ch_model", "ch_item_type",
	"ch_lifecycle_status", "ch_plm_status", "ch_approval_status",
	"ch_standard_cost", "ch_minimum_selling_price",
	"gst_hsn_code", "stock_uom", "brand", "country_of_origin",
	"has_batch_no", "has_serial_no", "is_stock_item",
	"ch_mrp_type", "ch_reorder_point", "ch_safety_stock_days",
	"ch_procurement_lead_days", "ch_lot_size", "ch_gtin",
]


def snapshot_item_version(doc, method=None):
	"""
	Called from Item on_update.  Writes a CH Item Version row capturing key
	fields at the moment of save.  Version number auto-increments per item.
	"""
	try:
		last_ver = frappe.db.get_value(
			"CH Item Version",
			{"item_code": doc.name},
			"version_number",
			order_by="version_number desc",
		) or 0

		snapshot = {f: getattr(doc, f, None) for f in _SNAPSHOT_FIELDS}

		ver = frappe.get_doc({
			"doctype": "CH Item Version",
			"item_code": doc.name,
			"version_number": last_ver + 1,
			"snapshot_date": now_datetime(),
			"changed_by": frappe.session.user,
			"ch_lifecycle_status": getattr(doc, "ch_lifecycle_status", None),
			"ch_plm_status": getattr(doc, "ch_plm_status", None),
			"ch_category": getattr(doc, "ch_category", None),
			"ch_sub_category": getattr(doc, "ch_sub_category", None),
			"ch_item_type": getattr(doc, "ch_item_type", None),
			"ch_standard_cost": flt(getattr(doc, "ch_standard_cost", 0)),
			"snapshot_json": json.dumps(snapshot, default=str),
		})
		ver.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Item version snapshot failed", message=frappe.get_traceback())


@frappe.whitelist()
def get_item_versions(item_code: str) -> list[dict]:
	"""Return all versions for an Item, newest first."""
	return frappe.get_all(
		"CH Item Version",
		filters={"item_code": item_code},
		fields=["name", "version_number", "snapshot_date", "changed_by",
		        "ch_lifecycle_status", "ch_plm_status", "ch_standard_cost", "remarks"],
		order_by="version_number desc",
	)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Formal Model Approval Gate
# ─────────────────────────────────────────────────────────────────────────────

_APPROVAL_BYPASS_ROLES = {"CH Master Approver", "System Manager", "Administrator"}

ApprovalError = frappe.ValidationError


def _user_roles() -> set[str]:
	return set(frappe.get_roles(frappe.session.user))


def enforce_approval_gate(doc, method=None):
	"""
	Called from Item before_save.
	Block setting lifecycle_status = Active unless ch_approval_status = Approved.
	Approver roles may bypass this check.
	"""
	approval = getattr(doc, "ch_approval_status", None) or "Draft"
	lifecycle = getattr(doc, "ch_lifecycle_status", None) or "Draft"

	if lifecycle == "Active" and approval != "Approved":
		if bool(_user_roles() & _APPROVAL_BYPASS_ROLES):
			frappe.msgprint(
				_("Approval Warning: item is being set Active without formal approval."),
				title=_("Approval Gate Bypassed"),
				indicator="orange",
			)
		else:
			frappe.throw(
				_("Item <b>{0}</b> cannot be set to Active until ch_approval_status = Approved. "
				  "Current approval status: <b>{1}</b>").format(doc.name, approval),
				title=_("Approval Gate"),
				exc=ApprovalError,
			)


@frappe.whitelist()
def submit_for_approval(item_code: str, remarks: str = "") -> str:
	"""Transition approval status Draft → Submitted for Review."""
	doc = frappe.get_doc("Item", item_code)
	if doc.ch_approval_status not in ("Draft", "Rejected"):
		frappe.throw(_("Can only submit items in Draft or Rejected state for review."))
	doc.ch_approval_status = "Submitted for Review"
	if remarks:
		doc.ch_approval_remarks = remarks
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)
	return "Submitted for Review"


@frappe.whitelist()
def approve_item(item_code: str, remarks: str = "") -> str:
	"""Approve an item that is Submitted for Review. Requires approver role."""
	if not bool(_user_roles() & _APPROVAL_BYPASS_ROLES):
		frappe.throw(_("Only CH Master Approver or System Manager can approve items."))
	doc = frappe.get_doc("Item", item_code)
	if doc.ch_approval_status != "Submitted for Review":
		frappe.throw(_("Item must be in 'Submitted for Review' state to approve."))
	doc.ch_approval_status = "Approved"
	doc.ch_approval_date = today()
	if remarks:
		doc.ch_approval_remarks = remarks
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)
	return "Approved"


@frappe.whitelist()
def reject_item(item_code: str, remarks: str = "") -> str:
	"""Reject an item that is Submitted for Review. Requires approver role."""
	if not bool(_user_roles() & _APPROVAL_BYPASS_ROLES):
		frappe.throw(_("Only CH Master Approver or System Manager can reject items."))
	doc = frappe.get_doc("Item", item_code)
	if doc.ch_approval_status != "Submitted for Review":
		frappe.throw(_("Item must be in 'Submitted for Review' state to reject."))
	doc.ch_approval_status = "Rejected"
	if remarks:
		doc.ch_approval_remarks = remarks
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)
	return "Rejected"


# ─────────────────────────────────────────────────────────────────────────────
# 3. GTIN validation
# ─────────────────────────────────────────────────────────────────────────────

GTINError = frappe.ValidationError


def validate_gtin(doc, method=None):
	"""
	Called from Item before_save.
	Validates that ch_gtin (if set) is a valid EAN-8, UPC-A (12-digit), EAN-13 (13-digit),
	or GTIN-14 (14-digit) using the standard check-digit algorithm.
	"""
	gtin = getattr(doc, "ch_gtin", None)
	if not gtin:
		return
	gtin = gtin.strip()
	if not gtin.isdigit() or len(gtin) not in (8, 12, 13, 14):
		frappe.throw(
			_("GTIN/EAN/UPC must be 8, 12, 13, or 14 digits (numeric). Got: {0}").format(gtin),
			title=_("Invalid GTIN"),
			exc=GTINError,
		)
	if not _gtin_check_digit_valid(gtin):
		frappe.throw(
			_("GTIN/EAN/UPC check digit is invalid for: {0}").format(gtin),
			title=_("Invalid GTIN Check Digit"),
			exc=GTINError,
		)


def _gtin_check_digit_valid(gtin: str) -> bool:
	"""Standard GS1 check digit algorithm (works for EAN-8/13, UPC-A, GTIN-14)."""
	digits = [int(d) for d in gtin]
	check = digits[-1]
	body = digits[:-1][::-1]
	total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
	expected = (10 - (total % 10)) % 10
	return check == expected


@frappe.whitelist()
def get_trading_partner_aliases(item_code: str) -> list[dict]:
	"""Return all trading partner aliases for an item."""
	return frappe.get_all(
		"CH Item Trading Partner Alias",
		filters={"parent": item_code, "parenttype": "Item"},
		fields=["partner_type", "partner", "partner_item_code", "partner_item_name", "is_primary"],
		order_by="partner_type asc, is_primary desc",
	)


# ─────────────────────────────────────────────────────────────────────────────
# 4. MRP / Coverage Planning — site-level defaults API
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_site_defaults(item_code: str, warehouse: str | None = None) -> list[dict] | dict | None:
	"""
	Return site-level MRP defaults for an item.
	If warehouse is supplied, return the matching row or None.
	Otherwise return all rows.
	"""
	rows = frappe.get_all(
		"CH Item Site Default",
		filters={"parent": item_code, "parenttype": "Item"},
		fields=["warehouse", "default_uom", "safety_stock", "reorder_point",
		        "lead_time_days", "min_order_qty"],
	)
	if warehouse:
		for r in rows:
			if r.warehouse == warehouse:
				return r
		return None
	return rows


# ─────────────────────────────────────────────────────────────────────────────
# 5. Vendor Info-Record API
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_vendor_info(item_code: str, supplier: str | None = None) -> list[dict] | dict | None:
	"""
	Return CH Vendor Info Record(s) for an item.
	If supplier is given, return the matching record or None.
	Returns preferred/active records first.
	"""
	filters: dict = {"item_code": item_code, "active": 1}
	if supplier:
		filters["supplier"] = supplier
	rows = frappe.get_all(
		"CH Vendor Info Record",
		filters=filters,
		fields=["name", "supplier", "vendor_item_code", "vendor_item_name",
		        "currency", "standard_price", "price_valid_from", "price_valid_to",
		        "lead_time_days", "min_order_qty", "preferred"],
		order_by="preferred desc, modified desc",
	)
	if supplier:
		return rows[0] if rows else None
	return rows


@frappe.whitelist()
def upsert_vendor_info(item_code: str, supplier: str, **kwargs) -> str:
	"""
	Create or update a CH Vendor Info Record for an item+supplier pair.
	Accepts same field names as the doctype.
	Returns the document name.
	"""
	existing = frappe.db.get_value(
		"CH Vendor Info Record",
		{"item_code": item_code, "supplier": supplier},
		"name",
	)
	if existing:
		doc = frappe.get_doc("CH Vendor Info Record", existing)
		for key, val in kwargs.items():
			if hasattr(doc, key):
				setattr(doc, key, val)
		doc.save(ignore_permissions=True)
		return doc.name
	else:
		doc = frappe.get_doc({
			"doctype": "CH Vendor Info Record",
			"item_code": item_code,
			"supplier": supplier,
			**kwargs,
		})
		doc.insert(ignore_permissions=True)
		return doc.name


# ─────────────────────────────────────────────────────────────────────────────
# 6. Full PLM State Machine
# ─────────────────────────────────────────────────────────────────────────────

# Valid PLM states (matches ch_plm_status Select options)
PLM_STATES = [
	"NPI",
	"Under Review",
	"Sample Testing",
	"Approved",
	"Active Production",
	"End of Life",
	"Discontinued",
]

# Allowed forward transitions (any state can also stay the same)
_PLM_TRANSITIONS: dict[str, set[str]] = {
	"NPI":               {"Under Review"},
	"Under Review":      {"Sample Testing", "NPI"},          # can revert to NPI
	"Sample Testing":    {"Approved", "Under Review"},       # can revert
	"Approved":          {"Active Production", "Under Review"},
	"Active Production": {"End of Life"},
	"End of Life":       {"Discontinued", "Active Production"},  # re-activation allowed
	"Discontinued":      set(),                              # terminal
}

# Transaction restrictions per PLM state
_PLM_BLOCK_PO: frozenset[str] = frozenset({"Discontinued"})
_PLM_BLOCK_SALE: frozenset[str] = frozenset({"NPI", "Under Review", "Sample Testing", "Discontinued"})
_PLM_WARN_SALE: frozenset[str] = frozenset({"End of Life"})
_PLM_BLOCK_PRODUCTION: frozenset[str] = frozenset({"NPI", "Discontinued"})

PLMError = frappe.ValidationError


def validate_plm_transition(doc, method=None):
	"""
	Called from Item before_save.
	Validates that the PLM state transition is allowed.
	Also stamps ch_plm_changed_on when the state changes.
	"""
	if doc.is_new():
		return
	plm_new = getattr(doc, "ch_plm_status", None) or "NPI"
	plm_old = frappe.db.get_value("Item", doc.name, "ch_plm_status") or "NPI"
	if plm_old == plm_new:
		return

	allowed = _PLM_TRANSITIONS.get(plm_old, set())
	if plm_new not in allowed:
		frappe.throw(
			_("PLM state transition <b>{0}</b> → <b>{1}</b> is not allowed. "
			  "Allowed next states: {2}").format(plm_old, plm_new, ", ".join(allowed) or "None"),
			title=_("Invalid PLM Transition"),
			exc=PLMError,
		)
	doc.ch_plm_changed_on = now_datetime()


def enforce_plm_on_transaction(doc, method=None):
	"""
	Called from Purchase Order, Sales Invoice, POS Invoice, Stock Entry validate.
	Blocks or warns based on PLM state per item.
	"""
	if not getattr(doc, "items", None):
		return

	doctype = doc.doctype
	errors = []
	warnings = []

	for row in doc.items:
		item_code = row.item_code
		if not item_code:
			continue
		plm = frappe.db.get_value("Item", item_code, "ch_plm_status") or "NPI"

		if doctype in ("Sales Invoice", "POS Invoice", "Sales Order", "Delivery Note"):
			if plm in _PLM_BLOCK_SALE:
				errors.append(
					_("Row {0}: Item <b>{1}</b> has PLM status <b>{2}</b> — sales are blocked.").format(
						row.idx, item_code, plm
					)
				)
			elif plm in _PLM_WARN_SALE:
				warnings.append(
					_("Row {0}: Item <b>{1}</b> is in <b>End of Life</b> — only sell-down stock is allowed.").format(
						row.idx, item_code
					)
				)

		elif doctype == "Purchase Order":
			if plm in _PLM_BLOCK_PO:
				errors.append(
					_("Row {0}: Item <b>{1}</b> has PLM status <b>{2}</b> — purchasing is blocked.").format(
						row.idx, item_code, plm
					)
				)

		elif doctype == "Stock Entry":
			if plm in _PLM_BLOCK_PRODUCTION:
				errors.append(
					_("Row {0}: Item <b>{1}</b> has PLM status <b>{2}</b> — production/stock entries are blocked.").format(
						row.idx, item_code, plm
					)
				)

	if errors:
		frappe.throw("<br>".join(errors), title=_("PLM Restriction"), exc=PLMError)
	if warnings:
		frappe.msgprint("<br>".join(warnings), title=_("PLM Warning — End of Life Items"), indicator="orange")
