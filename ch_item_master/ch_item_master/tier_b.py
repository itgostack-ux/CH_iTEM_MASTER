# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Item Master Tier B — SAP S/4HANA parity module.

Features:
  - Minimum Selling Price (MSP) enforcement on Sales Invoice / POS Invoice
  - Batch expiry / shelf-life enforcement on Delivery Note
  - Standard cost tracking + audit entry on change
  - UOM conversion defaults propagation from CH Sub Category to Item
  - Substitute items API helper (get active substitutes)
  - Completeness score API (0-100)

Wired from hooks.py doc_events.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, today, getdate, add_days

from ch_item_master.config import get_int_setting, has_role_setting
from ch_item_master.ch_item_master.exceptions import ItemNotActiveError


# ─────────────────────────────────────────────────────────────────────────────
# MSP Enforcement
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_APPROVAL_ROLES = {"CH Master Approver", "System Manager"}

MSPViolation = frappe.ValidationError


def enforce_msp(doc, method=None):
	"""
	Block or warn when any line item is priced below ch_minimum_selling_price.
	Approvers get a warning (msgprint); others get a hard block (throw).
	Called on: Sales Invoice validate, POS Invoice validate.
	"""
	if not getattr(doc, "items", None):
		return
	is_approver = has_role_setting("master_approval_roles", _DEFAULT_APPROVAL_ROLES)
	violations = []

	for row in doc.items:
		item_code = row.item_code
		if not item_code:
			continue
		msp = frappe.db.get_value("Item", item_code, "ch_minimum_selling_price") or 0
		if not msp:
			continue
		rate = flt(row.rate)
		if rate < flt(msp):
			violations.append(
				_("Row {0}: <b>{1}</b> rate {2} is below Minimum Selling Price {3}").format(
					row.idx, item_code, rate, msp
				)
			)

	if not violations:
		return

	msg = "<br>".join(violations)
	if is_approver:
		frappe.msgprint(
			_("MSP Warning (allowed for approvers):<br>{0}").format(msg),
			title=_("Below Minimum Selling Price"),
			indicator="orange",
		)
	else:
		frappe.throw(
			_("Cannot save — prices below Minimum Selling Price:<br>{0}").format(msg),
			title=_("MSP Violation"),
			exc=MSPViolation,
		)


# ─────────────────────────────────────────────────────────────────────────────
# Batch Expiry / Shelf-Life Enforcement (Delivery Note)
# ─────────────────────────────────────────────────────────────────────────────

ExpiryViolation = frappe.ValidationError


def enforce_expiry(doc, method=None):
	"""
	On Delivery Note validate: for every line whose item has has_expiry=1,
	check that the batch's expiry_date is in the future.
	Block if expired; warn if expiring within 7 days.
	"""
	if not getattr(doc, "items", None):
		return

	today_date = getdate(today())
	warning_days = get_int_setting("expiry_warning_days", 7)
	warn_threshold = add_days(today_date, warning_days)
	errors = []
	warnings = []
	item_codes = {row.item_code for row in doc.items if row.item_code}
	batch_names = {getattr(row, "batch_no", None) for row in doc.items if getattr(row, "batch_no", None)}
	item_rows = (
		frappe.get_all(
			"Item",
			filters={"name": ["in", list(item_codes)]},
			fields=["name", "has_batch_no", "ch_enforce_expiry"],
		)
		if item_codes
		else []
	)
	batch_rows = (
		frappe.get_all(
			"Batch", filters={"name": ["in", list(batch_names)]}, fields=["name", "expiry_date"]
		)
		if batch_names
		else []
	)
	items_by_name = {row.name: row for row in item_rows}
	batches_by_name = {row.name: row for row in batch_rows}

	for row in doc.items:
		item_code = row.item_code
		batch_no = getattr(row, "batch_no", None)
		if not item_code or not batch_no:
			continue

		item = items_by_name.get(item_code)
		has_expiry = item and item.has_batch_no and item.ch_enforce_expiry
		if not has_expiry:
			continue

		batch = batches_by_name.get(batch_no)
		expiry_date = batch.expiry_date if batch else None
		if not expiry_date:
			continue

		expiry_date = getdate(expiry_date)
		if expiry_date < today_date:
			errors.append(
				_("Row {0}: Batch <b>{1}</b> for item <b>{2}</b> expired on {3}").format(
					row.idx, batch_no, item_code, expiry_date
				)
			)
		elif expiry_date <= warn_threshold:
			warnings.append(
				_("Row {0}: Batch <b>{1}</b> for item <b>{2}</b> expires on {3} (within {4} days)").format(
					row.idx, batch_no, item_code, expiry_date, warning_days
				)
			)

	if errors:
		frappe.throw("<br>".join(errors), title=_("Expired Batch"), exc=ExpiryViolation)
	if warnings:
		frappe.msgprint("<br>".join(warnings), title=_("Near-Expiry Batches"), indicator="orange")


# ─────────────────────────────────────────────────────────────────────────────
# Standard Cost Tracking
# ─────────────────────────────────────────────────────────────────────────────

def track_standard_cost(doc, method=None):
	"""
	Called from Item on_update. If ch_standard_cost changed, write an audit row.
	"""
	if doc.is_new():
		return
	try:
		old_cost = frappe.db.get_value("Item", doc.name, "ch_standard_cost") or 0
		new_cost = flt(getattr(doc, "ch_standard_cost", 0))
		if old_cost == new_cost:
			return
		from ch_item_master.ch_item_master.governance import write_audit
		write_audit(
			doc.name,
			"Standard Cost Changed",
			field_name="ch_standard_cost",
			old_value=str(old_cost),
			new_value=str(new_cost),
		)
	except Exception:
		frappe.log_error(title="Standard cost audit failed", message=frappe.get_traceback())


# ─────────────────────────────────────────────────────────────────────────────
# UOM Conversion Defaults Propagation
# ─────────────────────────────────────────────────────────────────────────────

def apply_uom_defaults(doc, method=None):
	"""
	On Item before_insert: copy UOM conversion rows from CH Sub Category
	defaults if the item has no existing uoms table entries.
	ERPNext stores UOM conversions in tabUOM Conversion Detail (child of Item).
	"""
	if not doc.is_new():
		return
	sc = getattr(doc, "ch_sub_category", None)
	if not sc:
		return
	# Only copy if the item doesn't already have conversions set
	if getattr(doc, "uoms", None) and len(doc.uoms) > 1:
		return
	try:
		sc_doc = frappe.get_doc("CH Sub Category", sc)
		if not getattr(sc_doc, "ch_uom_conversions", None):
			return
		existing_uoms = {row.uom for row in (doc.uoms or [])}
		for rule in sc_doc.ch_uom_conversions:
			if rule.to_uom and rule.to_uom not in existing_uoms:
				doc.append("uoms", {
					"uom": rule.to_uom,
					"conversion_factor": rule.conversion_factor or 1,
				})
				existing_uoms.add(rule.to_uom)
	except Exception:
		frappe.log_error(title="UOM defaults propagation failed", message=frappe.get_traceback())


# ─────────────────────────────────────────────────────────────────────────────
# Substitutes API
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_active_substitutes(item_code: str) -> list[dict]:
	"""
	Return active substitute items for the given item_code.
	Active = no effective_to OR effective_to >= today.
	"""
	today_str = today()
	rows = frappe.get_all(
		"CH Item Substitute",
		filters={
			"parent": item_code,
			"parenttype": "Item",
		},
		fields=["substitute_item", "substitute_type", "priority", "effective_from", "effective_to", "notes"],
		order_by="priority asc",
	)
	active = []
	for r in rows:
		if r.effective_to and getdate(r.effective_to) < getdate(today_str):
			continue
		if r.effective_from and getdate(r.effective_from) > getdate(today_str):
			continue
		active.append(r)
	return active


# ─────────────────────────────────────────────────────────────────────────────
# Completeness Score
# ─────────────────────────────────────────────────────────────────────────────

_COMPLETENESS_CHECKS = [
	# (field, weight, description)
	("gst_hsn_code",          15, "GST/HSN Code"),
	("stock_uom",             10, "Stock UOM"),
	("ch_category",           10, "Category"),
	("ch_sub_category",       10, "Sub Category"),
	("ch_lifecycle_status",    5, "Lifecycle Status"),
	("item_group",             5, "Item Group"),
	("description",            5, "Description"),
	("ch_standard_cost",       5, "Standard Cost"),
	("ch_minimum_selling_price", 5, "Minimum Selling Price"),
	("brand",                  5, "Brand"),
	("country_of_origin",      5, "Country of Origin"),
	("ch_item_type",           5, "Item Type (New/Refurbished)"),
	("shelf_life_in_days",     5, "Shelf Life Days (if perishable)"),
	("weight_per_unit",        5, "Weight"),
	("has_batch_no",           5, "Batch Tracking configured"),
]


@frappe.whitelist()
def get_completeness_score(item_code: str) -> dict:
	"""
	Return a 0-100 completeness score and breakdown for an Item.
	Used by the monitoring dashboard and the Item form sidebar.
	"""
	if not frappe.db.exists("Item", item_code):
		frappe.throw(_("Item {0} not found").format(item_code))

	item = frappe.get_doc("Item", item_code)
	total_weight = sum(w for _, w, _ in _COMPLETENESS_CHECKS)
	score = 0
	breakdown = []
	for field, weight, label in _COMPLETENESS_CHECKS:
		val = getattr(item, field, None)
		filled = bool(val) and val not in ("", 0, None)
		if filled:
			score += weight
		breakdown.append({"field": field, "label": label, "filled": filled, "weight": weight})

	pct = round((score / total_weight) * 100, 1)
	grade_a_min = get_int_setting("item_quality_grade_a_min", 90)
	grade_b_min = get_int_setting("item_quality_grade_b_min", 70)
	grade_c_min = get_int_setting("item_quality_grade_c_min", 50)
	return {
		"item_code": item_code,
		"score": pct,
		"filled_weight": score,
		"total_weight": total_weight,
		"breakdown": breakdown,
		"grade": (
			"A" if pct >= grade_a_min
			else "B" if pct >= grade_b_min
			else "C" if pct >= grade_c_min
			else "D"
		),
	}
