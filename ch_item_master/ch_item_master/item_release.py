# Copyright (c) 2026, GoStack and contributors

"""Bulk item release — the governed way to move a catalogue into production.

An item is created Draft / NPI on purpose: a half-configured master must not
transact. Governance enforces that at the point of use, so an unreleased item
fails at Stock Entry or on an invoice with a per-item error.

That is correct, but it is not a workflow. Releasing a catalogue one item at a
time through the form is not viable for a bulk import — 1,000 spares loaded
from a supplier price list all sit Draft/NPI and every repair that needs one
fails at the till.

Every mainstream ERP answers this the same way, and so does this module:

  * a READINESS CHECK first — an item is only releasable when it carries the
    master data a transaction needs (UOM, item group, a tax code, a price or a
    valuation). The check reports WHY an item is not ready instead of letting
    it fail later at a Stock Entry.
  * release through the EXISTING APPROVAL GATE, not by writing the status
    columns. ``tier_c.submit_for_approval`` / ``approve_item`` carry the
    segregation-of-duties rules, the PLM transition validation and the audit
    fields; bypassing them would leave a released item with no record of who
    released it.
  * a DRY RUN, because releasing a thousand items is not something to discover
    the shape of afterwards.

Segregation of duties is NOT weakened. ``check_sod`` already exempts holders of
``break_glass_supervisor_roles`` (and Administrator), which is the existing,
audited way to perform an operation that has no second reviewer — a bulk
catalogue load being exactly that. Run the release as such a user; the reason
you pass is written to each item's approval remarks as the record of why.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from ch_item_master.config import require_role_setting

RELEASED_LIFECYCLE = "Active"
RELEASABLE_FROM = ("", "Draft", "Submitted for Review")


def check_item_readiness(item_code: str) -> dict:
	"""Return {ready, blockers} for one item.

	Blockers are the master data a transaction will demand later. Catching them
	here turns "Item X is in lifecycle status Draft" at the counter into a list
	an operator can actually work through.
	"""
	item = frappe.db.get_value(
		"Item",
		item_code,
		[
			"name", "item_name", "item_group", "stock_uom", "disabled",
			"is_stock_item", "gst_hsn_code", "valuation_rate", "standard_rate",
			"ch_lifecycle_status", "ch_plm_status", "ch_approval_status",
		],
		as_dict=True,
	)
	if not item:
		return {"ready": False, "blockers": [_("Item does not exist")], "item": item_code}

	blockers = []
	if cint(item.disabled):
		blockers.append(_("Item is disabled"))
	if not item.item_group:
		blockers.append(_("No Item Group"))
	if not item.stock_uom:
		blockers.append(_("No stock UOM"))
	if not item.gst_hsn_code:
		blockers.append(_("No HSN/SAC code — the invoice would be non-compliant"))

	if not (flt(item.standard_rate) or _has_selling_price(item_code)):
		blockers.append(_("No selling price (standard rate or Item Price)"))

	# Costing gaps are WARNINGS, not blockers. ERPNext derives valuation from the
	# first receipt, so demanding a rate up front would hold a whole catalogue
	# hostage to data the first GRN supplies anyway. It is still reported,
	# because until it lands COGS posts at zero and margin is fiction.
	warnings = []
	if cint(item.is_stock_item) and not flt(item.valuation_rate) and not _has_stock(item_code):
		warnings.append(
			_("No valuation rate yet — COGS and margin will read zero until the "
			  "first receipt sets one")
		)

	return {
		"ready": not blockers,
		"blockers": blockers,
		"warnings": warnings,
		"item": item_code,
		"detail": item,
	}


@frappe.whitelist()
def preview_release(item_codes=None, filters=None, limit: int = 2000) -> dict:
	"""Dry run: what would be released, and what is holding the rest back."""
	codes = _resolve_codes(item_codes, filters, limit)
	ready, blocked = [], []
	for code in codes:
		result = check_item_readiness(code)
		(ready if result["ready"] else blocked).append(result)

	reasons, cautions = {}, {}
	for item in blocked:
		for blocker in item["blockers"]:
			reasons[blocker] = reasons.get(blocker, 0) + 1
	for item in ready + blocked:
		for warning in item.get("warnings") or []:
			cautions[warning] = cautions.get(warning, 0) + 1

	return {
		"considered": len(codes),
		"ready": len(ready),
		"blocked": len(blocked),
		"reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
		"warnings": dict(sorted(cautions.items(), key=lambda kv: -kv[1])),
		"ready_items": [item["item"] for item in ready][:200],
		"blocked_items": [
			{"item": item["item"], "blockers": item["blockers"]} for item in blocked
		][:200],
	}


@frappe.whitelist(methods=["POST"])
def release_items(item_codes=None, filters=None, remarks: str = "",
                  limit: int = 2000) -> dict:
	"""Release ready items through the approval gate. Skips anything not ready."""
	# Who may release a catalogue is a policy decision, so it lives in
	# CH Item Master Settings rather than in this file. plm_manager_roles is the
	# existing setting for moving items through their lifecycle, which is exactly
	# what a bulk release does. get_role_setting always folds in the privileged
	# roles, so an unconfigured site still lets an administrator through.
	require_role_setting("plm_manager_roles", action=_("release items to production"))
	if not remarks:
		frappe.throw(
			_("A release reason is required — it is written to the item's approval "
			  "remarks and is the audit trail for why the catalogue went live."),
			title=_("Reason Required"),
		)

	from ch_item_master.ch_item_master.tier_c import approve_item, submit_for_approval

	codes = _resolve_codes(item_codes, filters, limit)
	released, skipped, failed = [], [], []

	for code in codes:
		result = check_item_readiness(code)
		if not result["ready"]:
			skipped.append({"item": code, "blockers": result["blockers"]})
			continue

		detail = result["detail"]
		if detail.ch_lifecycle_status == RELEASED_LIFECYCLE and detail.ch_plm_status in (
			"Approved", "Active Production"
		):
			continue  # already live

		try:
			if (detail.ch_approval_status or "") in ("", "Draft", "Rejected"):
				submit_for_approval(code, remarks=remarks)
			# SoD still applies: approve_item -> check_sod exempts only
			# break_glass_supervisor_roles / Administrator, so a bulk release
			# must be run by someone actually authorised to self-approve.
			approve_item(code, remarks=remarks)
			released.append(code)
		except Exception as exc:
			failed.append({"item": code, "error": str(exc)[:200]})

	frappe.db.commit()
	return {
		"considered": len(codes),
		"released": len(released),
		"skipped_not_ready": len(skipped),
		"failed": len(failed),
		"skipped_detail": skipped[:100],
		"failed_detail": failed[:100],
	}


# ── helpers ──────────────────────────────────────────────────────────────────

def _resolve_codes(item_codes, filters, limit) -> list:
	if isinstance(item_codes, str):
		item_codes = frappe.parse_json(item_codes)
	if item_codes:
		return list(item_codes)[:limit]
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	return frappe.get_all(
		"Item", filters=filters or {"ch_lifecycle_status": ("!=", RELEASED_LIFECYCLE)},
		pluck="name", limit_page_length=limit,
	)


def _has_selling_price(item_code) -> bool:
	return bool(frappe.db.exists("Item Price", {"item_code": item_code, "selling": 1}))


def _has_stock(item_code) -> bool:
	return bool(
		frappe.db.get_value("Bin", {"item_code": item_code, "actual_qty": (">", 0)}, "name")
	)
