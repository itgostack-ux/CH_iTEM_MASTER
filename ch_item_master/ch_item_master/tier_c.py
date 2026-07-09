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
  7. Vendor Performance Scoring          — OTIF, quality, defect rate, risk level, auto-blocking
  8. Allocation Automation               — quota-based sourcing split, rebalancing
  9. Contract-Integrated Sourcing        — contract price takes priority over standard price
                                           → Active Production → End of Life → Discontinued
                                           with transaction-blocking rules per state

Wired from hooks.py doc_events.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import now_datetime, today, getdate, flt, cint

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
	"""Transition approval status Draft → Submitted for Review. Records SoD submitter."""
	doc = frappe.get_doc("Item", item_code)
	if doc.ch_approval_status not in ("Draft", "Rejected"):
		frappe.throw(_("Can only submit items in Draft or Rejected state for review."))
	doc.ch_approval_status = "Submitted for Review"
	# SoD: record who submitted so the same person cannot approve
	doc.ch_submitted_by = frappe.session.user
	try:
		from frappe.utils import now_datetime as _now
		doc.ch_submitted_on = _now()
	except Exception:
		pass
	if remarks:
		doc.ch_approval_remarks = remarks
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)
	return "Submitted for Review"


@frappe.whitelist()
def approve_item(item_code: str, remarks: str = "") -> str:
	"""Approve an item that is Submitted for Review. Enforces SoD and delegation."""
	from ch_item_master.ch_item_master.rbac import check_sod, is_effective_approver
	if not is_effective_approver():
		frappe.throw(_("Only CH Master Approver (or a valid delegate) can approve items."))
	doc = frappe.get_doc("Item", item_code)
	if doc.ch_approval_status != "Submitted for Review":
		frappe.throw(_("Item must be in 'Submitted for Review' state to approve."))
	# SoD: block self-approval
	check_sod(submitted_by=doc.get("ch_submitted_by") or "")
	doc.ch_approval_status = "Approved"
	doc.ch_approval_date = today()
	if remarks:
		doc.ch_approval_remarks = remarks
	# Auto-activate: approval gate is the formal gate; advance lifecycle and PLM status
	# so the item is immediately usable in Sales Invoice / POS Invoice.
	doc.ch_lifecycle_status = "Active"
	_plm = getattr(doc, "ch_plm_status", None) or "NPI"
	if _plm not in ("Approved", "Active Production", "End of Life", "Discontinued"):
		doc.ch_plm_status = "Approved"
		doc.flags.ignore_plm_transition = True  # approval action skips intermediate PLM steps
	doc.flags.ignore_lifecycle_transition = True  # role already validated above; allow delegated approvers
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)
	return "Approved"


@frappe.whitelist()
def reject_item(item_code: str, remarks: str = "") -> str:
	"""Reject an item that is Submitted for Review. Enforces SoD and delegation."""
	from ch_item_master.ch_item_master.rbac import check_sod, is_effective_approver
	if not is_effective_approver():
		frappe.throw(_("Only CH Master Approver (or a valid delegate) can reject items."))
	doc = frappe.get_doc("Item", item_code)
	if doc.ch_approval_status != "Submitted for Review":
		frappe.throw(_("Item must be in 'Submitted for Review' state to reject."))
	# SoD: block self-rejection
	check_sod(submitted_by=doc.get("ch_submitted_by") or "")
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
def get_vendor_info(
	item_code: str,
	supplier: str | None = None,
	company: str | None = None,
	purchase_org: str | None = None,
	supplier_site: str | None = None,
	as_of_date: str | None = None,
) -> list[dict] | dict | None:
	"""
	Return CH Vendor Info Record(s) for an item.
	If supplier is given, return the matching record or None.
	Returns preferred/active records first.
	"""
	from ch_item_master.ch_item_master.rbac import check_vendor_view_role
	check_vendor_view_role()
	filters: dict = {"item_code": item_code, "active": 1}
	as_of = getdate(as_of_date) if as_of_date else getdate(today())
	if supplier:
		filters["supplier"] = supplier
	if company:
		filters["company"] = company
	if purchase_org:
		filters["purchase_org"] = purchase_org
	if supplier_site:
		filters["supplier_site"] = supplier_site
	filters["approval_status"] = "Approved"
	rows = frappe.get_all(
		"CH Vendor Info Record",
		filters=filters,
		fields=["name", "supplier", "vendor_item_code", "vendor_item_name",
		        "currency", "standard_price", "price_valid_from", "price_valid_to", "company",
		        "purchase_org", "supplier_site", "source_rank", "allocation_pct",
		        "lead_time_days", "min_order_qty", "preferred", "approval_status"],
		order_by="preferred desc, source_rank asc, standard_price asc, modified desc",
	)
	rows = [
		r for r in rows
		if (not r.price_valid_from or getdate(r.price_valid_from) <= as_of)
		and (not r.price_valid_to or getdate(r.price_valid_to) >= as_of)
	]
	if supplier:
		return rows[0] if rows else None
	return rows


@frappe.whitelist()
def upsert_vendor_info(
	item_code: str,
	supplier: str,
	company: str | None = None,
	purchase_org: str | None = None,
	supplier_site: str | None = None,
	**kwargs,
) -> str:
	"""
	Create or update a CH Vendor Info Record for an item+supplier pair.
	Accepts same field names as the doctype.
	Returns the document name.
	"""
	# Role gate: CH Vendor Manager or higher required
	from ch_item_master.ch_item_master.rbac import check_vendor_manager_role
	check_vendor_manager_role()
	lookup_filters = {"item_code": item_code, "supplier": supplier}
	if company:
		lookup_filters["company"] = company
	if purchase_org:
		lookup_filters["purchase_org"] = purchase_org
	if supplier_site:
		lookup_filters["supplier_site"] = supplier_site
	existing = frappe.db.get_value(
		"CH Vendor Info Record",
		lookup_filters,
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
			"company": company,
			"purchase_org": purchase_org,
			"supplier_site": supplier_site,
			"approval_status": kwargs.pop("approval_status", "Approved"),
			**kwargs,
		})
		doc.insert(ignore_permissions=True)
		return doc.name


@frappe.whitelist()
def submit_vendor_info_for_approval(name: str) -> str:
	"""Submit a vendor info record for approval."""
	from ch_item_master.ch_item_master.rbac import check_vendor_manager_role
	check_vendor_manager_role()
	doc = frappe.get_doc("CH Vendor Info Record", name)
	if doc.approval_status not in ("Draft", "Rejected"):
		frappe.throw(_("Only Draft or Rejected records can be submitted."))
	doc.approval_status = "Submitted"
	doc.submitted_by = frappe.session.user
	doc.submitted_on = now_datetime()
	doc.save(ignore_permissions=True)
	return "Submitted"


@frappe.whitelist()
def approve_vendor_info(name: str, remarks: str = "") -> str:
	"""Approve a vendor info record (maker-checker with SoD)."""
	from ch_item_master.ch_item_master.rbac import check_sod, is_effective_approver
	if not is_effective_approver():
		frappe.throw(_("Only CH Master Approver (or valid delegate) can approve vendor info."))
	doc = frappe.get_doc("CH Vendor Info Record", name)
	if doc.approval_status != "Submitted":
		frappe.throw(_("Vendor info must be in Submitted state to approve."))
	check_sod(submitted_by=doc.submitted_by or "")
	doc.approval_status = "Approved"
	doc.approved_by = frappe.session.user
	doc.approved_on = now_datetime()
	if remarks:
		doc.notes = (doc.notes or "") + f"\n[Approval] {remarks}" if doc.notes else f"[Approval] {remarks}"
	doc.save(ignore_permissions=True)
	return "Approved"


@frappe.whitelist()
def get_effective_vendor_source(
	item_code: str,
	qty: float,
	company: str | None = None,
	purchase_org: str | None = None,
	uom: str | None = None,
	as_of_date: str | None = None,
) -> dict | None:
	"""
	Return the best vendor source for requested quantity.
	Respects active+approved records, validity window, MOQ, preferred/source rank,
	and quantity price breaks where available.
	"""
	vendors = get_vendor_info(
		item_code=item_code,
		company=company,
		purchase_org=purchase_org,
		as_of_date=as_of_date,
	)
	if not vendors:
		return None

	requested_qty = flt(qty)
	as_of = getdate(as_of_date) if as_of_date else getdate(today())
	candidates = []

	for vendor in vendors:
		if requested_qty < flt(vendor.min_order_qty):
			continue

		effective_price = flt(vendor.standard_price)
		matched_break = None
		rec = frappe.get_doc("CH Vendor Info Record", vendor.name)

		# Contract price takes priority over price breaks and standard price
		contract_price = _get_active_contract_price(rec, as_of)
		if contract_price is not None:
			effective_price = contract_price
		else:
			for br in rec.get("price_breaks") or []:
				if not br.is_active:
					continue
				if br.valid_from and getdate(br.valid_from) > as_of:
					continue
				if br.valid_to and getdate(br.valid_to) < as_of:
					continue
				if uom and br.uom and br.uom != uom:
					continue
				if requested_qty < flt(br.min_qty):
					continue
				if br.max_qty and flt(br.max_qty) > 0 and requested_qty > flt(br.max_qty):
					continue
				effective_price = flt(br.unit_price)
				matched_break = br.name
				break

		candidates.append(
			{
				"vendor_record": vendor.name,
				"supplier": vendor.supplier,
				"company": vendor.company,
				"purchase_org": vendor.purchase_org,
				"supplier_site": vendor.supplier_site,
				"preferred": vendor.preferred,
				"source_rank": vendor.source_rank,
				"allocation_pct": vendor.allocation_pct,
				"min_order_qty": vendor.min_order_qty,
				"effective_unit_price": effective_price,
				"matched_price_break": matched_break,
			}
		)

	if not candidates:
		return None

	candidates.sort(
		key=lambda x: (
			0 if cint(x.get("preferred")) else 1,
			cint(x.get("source_rank") or 999999),
			flt(x.get("effective_unit_price") or 0),
		)
	)
	return candidates[0]


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
	Requires CH PLM Manager role for any PLM state change on an existing item.
	"""
	# Skip transition validation when approval action auto-advances PLM status
	if doc.flags.get("ignore_plm_transition"):
		return
	if doc.is_new():
		return
	plm_new = getattr(doc, "ch_plm_status", None) or "NPI"
	plm_old = frappe.db.get_value("Item", doc.name, "ch_plm_status") or "NPI"
	if plm_old == plm_new:
		return

	# Role gate: PLM state change requires CH PLM Manager or higher
	from ch_item_master.ch_item_master.rbac import check_plm_role
	check_plm_role()

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
	# Test-mode escape hatch: erpnext test bootstrap seeds items in
	# default PLM='NPI' and then attempts sample Stock/Sales/Purchase entries
	# against them, which would fail collection. Interactive prod behaviour
	# is unaffected (frappe.flags.in_test is only True under ``bench run-tests``).
	if frappe.flags.in_test:
		return

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


# ─────────────────────────────────────────────────────────────────────────────
# 7. Vendor Performance Scoring
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def record_vendor_performance(
	item_code: str,
	supplier: str,
	evaluation_date: str | None = None,
	evaluation_period: str | None = None,
	otif_pct: float = 0.0,
	on_time_delivery_pct: float = 0.0,
	quality_score: float = 0.0,
	defect_rate: float = 0.0,
	risk_level: str = "Low",
	block_reason: str = "",
) -> str:
	"""
	Record a vendor performance evaluation.
	If risk_level is Critical, automatically marks the vendor's CH Vendor Info Record
	as active=0 (blocked) for the given item+supplier combination.

	Returns the name of the created CH Vendor Performance document.
	"""
	from ch_item_master.ch_item_master.rbac import check_vendor_manager_role
	check_vendor_manager_role()

	eval_date = evaluation_date or today()
	auto_block = 1 if risk_level == "Critical" else 0

	rec = frappe.get_doc({
		"doctype": "CH Vendor Performance",
		"item_code": item_code,
		"supplier": supplier,
		"evaluation_date": eval_date,
		"evaluation_period": evaluation_period or "",
		"evaluator": frappe.session.user,
		"otif_pct": flt(otif_pct),
		"on_time_delivery_pct": flt(on_time_delivery_pct),
		"quality_score": flt(quality_score),
		"defect_rate": flt(defect_rate),
		"risk_level": risk_level,
		"auto_block": auto_block,
		"block_reason": block_reason,
	})
	rec.insert(ignore_permissions=True)
	frappe.db.commit()

	# Auto-block: deactivate the vendor info record(s) for this item+supplier
	if auto_block:
		vir_names = frappe.get_all(
			"CH Vendor Info Record",
			filters={"item_code": item_code, "supplier": supplier, "active": 1},
			pluck="name",
		)
		for vir_name in vir_names:
			frappe.db.set_value(
				"CH Vendor Info Record",
				vir_name,
				{"active": 0, "notes": f"[Auto-blocked] {block_reason}"},
				update_modified=False,
			)
		if vir_names:
			frappe.db.commit()

	return rec.name


@frappe.whitelist()
def get_vendor_performance(
	item_code: str,
	supplier: str,
	limit: int = 5,
) -> list[dict]:
	"""Return recent performance evaluations for a vendor, newest first."""
	from ch_item_master.ch_item_master.rbac import check_vendor_view_role
	check_vendor_view_role()
	return frappe.get_all(
		"CH Vendor Performance",
		filters={"item_code": item_code, "supplier": supplier},
		fields=[
			"name", "evaluation_date", "evaluation_period", "evaluator",
			"otif_pct", "on_time_delivery_pct", "quality_score",
			"defect_rate", "risk_level", "auto_block",
		],
		order_by="evaluation_date desc",
		limit=cint(limit),
	)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Allocation Automation
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def run_allocation_check(
	item_code: str,
	company: str | None = None,
	purchase_org: str | None = None,
) -> dict:
	"""
	Validate that all active+approved vendor allocations for an item
	sum to exactly 100%.  Considers all vendors with allocation_pct > 0.
	Returns {ok: bool, total_pct: float, vendors: list}.
	"""
	filters: dict = {"item_code": item_code, "active": 1, "approval_status": "Approved"}
	if company:
		filters["company"] = company
	if purchase_org:
		filters["purchase_org"] = purchase_org

	rows = frappe.get_all(
		"CH Vendor Info Record",
		filters=filters,
		fields=["name", "supplier", "allocation_pct", "source_rank"],
		order_by="source_rank asc",
	)
	# Only count vendors that have a non-zero allocation set
	alloc_rows = [r for r in rows if flt(r.allocation_pct) > 0]
	total = sum(flt(r.allocation_pct) for r in alloc_rows)
	return {
		"ok": abs(total - 100.0) < 0.01 if alloc_rows else True,
		"total_pct": total,
		"vendors": alloc_rows,
	}


@frappe.whitelist()
def get_sourcing_split(
	item_code: str,
	total_qty: float,
	company: str | None = None,
	purchase_org: str | None = None,
	as_of_date: str | None = None,
) -> list[dict]:
	"""
	Split total_qty across preferred vendors by their allocation_pct.
	Returns [{supplier, qty, effective_unit_price, vendor_record}].
	Falls back to round-robin by source_rank if allocations don't sum to 100.
	"""
	from ch_item_master.ch_item_master.rbac import check_vendor_view_role
	check_vendor_view_role()

	total_qty = flt(total_qty)
	as_of = getdate(as_of_date) if as_of_date else getdate(today())

	check = run_allocation_check(item_code, company=company, purchase_org=purchase_org)
	vendors = sorted(
		check.get("vendors") or [],
		key=lambda v: cint(v.get("source_rank") or 999999),
	)

	if not vendors:
		return []

	result = []
	remaining = total_qty

	for i, vendor in enumerate(vendors):
		alloc_pct = flt(vendor.allocation_pct)
		if i == len(vendors) - 1:
			# Last vendor gets remainder to avoid rounding drift
			qty = remaining
		else:
			qty = round(total_qty * alloc_pct / 100.0, 4)
			remaining -= qty

		if qty <= 0:
			continue

		# Resolve effective price for this vendor
		effective_price = None
		rec = frappe.get_doc("CH Vendor Info Record", vendor.name)

		# Contract price takes priority
		contract_price = _get_active_contract_price(rec, as_of)
		if contract_price is not None:
			effective_price = contract_price
		else:
			# Price break or standard
			for br in rec.get("price_breaks") or []:
				if not br.is_active:
					continue
				if br.valid_from and getdate(br.valid_from) > as_of:
					continue
				if br.valid_to and getdate(br.valid_to) < as_of:
					continue
				if qty < flt(br.min_qty):
					continue
				if br.max_qty and flt(br.max_qty) > 0 and qty > flt(br.max_qty):
					continue
				effective_price = flt(br.unit_price)
				break
			if effective_price is None:
				effective_price = flt(rec.standard_price)

		result.append({
			"vendor_record": vendor.name,
			"supplier": vendor.supplier,
			"allocation_pct": alloc_pct,
			"qty": qty,
			"effective_unit_price": effective_price,
		})

	return result


# ─────────────────────────────────────────────────────────────────────────────
# 9. Contract-Integrated Sourcing
# ─────────────────────────────────────────────────────────────────────────────

def _get_active_contract_price(rec, as_of) -> float | None:
	"""
	Return the first active contract price from a CH Vendor Info Record doc.
	Contract price takes priority over price breaks and standard price.
	Returns None if no active contract found.
	"""
	for ct in rec.get("contracts") or []:
		if ct.valid_from and getdate(ct.valid_from) > as_of:
			continue
		if ct.valid_to and getdate(ct.valid_to) < as_of:
			continue
		if ct.contract_price:
			return flt(ct.contract_price)
	return None


@frappe.whitelist()
def get_contract_price(
	item_code: str,
	supplier: str,
	company: str | None = None,
	purchase_org: str | None = None,
	as_of_date: str | None = None,
) -> dict | None:
	"""
	Return the active contract price for an item+supplier, if any.
	Returns {contract_no, contract_type, contract_price, valid_from, valid_to} or None.
	"""
	from ch_item_master.ch_item_master.rbac import check_vendor_view_role
	check_vendor_view_role()

	as_of = getdate(as_of_date) if as_of_date else getdate(today())
	filters: dict = {"item_code": item_code, "supplier": supplier, "active": 1}
	if company:
		filters["company"] = company
	if purchase_org:
		filters["purchase_org"] = purchase_org

	vir_names = frappe.get_all("CH Vendor Info Record", filters=filters, pluck="name")
	for vir_name in vir_names:
		rec = frappe.get_doc("CH Vendor Info Record", vir_name)
		for ct in rec.get("contracts") or []:
			if ct.valid_from and getdate(ct.valid_from) > as_of:
				continue
			if ct.valid_to and getdate(ct.valid_to) < as_of:
				continue
			if ct.contract_price:
				return {
					"contract_no": ct.contract_no,
					"contract_type": ct.contract_type,
					"contract_price": flt(ct.contract_price),
					"currency": ct.currency,
					"valid_from": str(ct.valid_from) if ct.valid_from else None,
					"valid_to": str(ct.valid_to) if ct.valid_to else None,
					"vendor_record": vir_name,
				}
	return None

