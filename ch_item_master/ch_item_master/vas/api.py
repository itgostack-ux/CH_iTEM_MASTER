"""VAS catalog APIs — thin redirects to CH Warranty Plan.

Historical context: this module used to query the deprecated ``VAS
Product`` / ``VAS Plan`` / ``VAS Attach Rule`` / ``VAS Claim``
doctypes, which mirrored ``CH Warranty Plan`` / ``CH Attach Rule`` /
``CH Warranty Claim``. Those wrappers have been merged back into
their source doctypes (see
``ch_item_master.patches.v31_merge_vas_plan_into_ch_warranty_plan``).

These whitelisted endpoints are kept so any front-end still calling
``ch_item_master.ch_item_master.vas.api.*`` continues to work — they
now query the CH Warranty Plan / CH Warranty Claim / CH Attach Rule
surfaces directly.

``VAS Commission`` and ``VAS Partner`` are separate doctypes that
were NOT merged and continue to work as-is.
"""

import frappe
from frappe import _

from ch_item_master.config import require_role_setting


@frappe.whitelist()
def get_vas_product_catalog(limit=50):
	"""Return sellable CH Warranty Plans as a product-style catalog.

	VAS Product used to mirror the plan's service_item as a
	separate SKU. Post-merge the plan owns the service_item link
	directly, so this endpoint just filters CH Warranty Plans that
	are marked ``is_sellable=1``.
	"""
	limit = min(int(limit or 50), 200)
	frappe.has_permission("CH Warranty Plan", "read", throw=True)
	return frappe.get_list(
		"CH Warranty Plan",
		filters={"status": "Active", "is_sellable": 1},
		fields=[
			"name AS name",
			"plan_name AS product_name",
			"service_item",
			"plan_type AS product_type",
			"brand",
		],
		order_by="modified desc",
		limit=limit,
	)


@frappe.whitelist()
def get_vas_plan_catalog(limit=100):
	"""Return sellable CH Warranty Plans."""
	limit = min(int(limit or 100), 300)
	frappe.has_permission("CH Warranty Plan", "read", throw=True)
	return frappe.get_list(
		"CH Warranty Plan",
		filters={"status": "Active", "is_sellable": 1},
		fields=[
			"name", "plan_name", "service_item", "price",
			"duration_months", "plan_type", "fulfillment_type",
			"partner", "min_device_price", "max_device_price",
		],
		order_by="modified desc",
		limit=limit,
	)


@frappe.whitelist()
def get_vas_claims(limit=100):
	"""Return CH Warranty Claims (the single claim surface post-merge)."""
	limit = min(int(limit or 100), 300)
	frappe.has_permission("CH Warranty Claim", "read", throw=True)
	return frappe.get_list(
		"CH Warranty Claim",
		fields=[
			"name", "claim_date", "customer", "sold_plan",
			"claim_status", "approved_amount",
		],
		order_by="modified desc",
		limit=limit,
	)


@frappe.whitelist()
def get_vas_attach_offers(item_code: str, selling_price=None, company=None):
	"""Delegate to the single CH Attach Rule surface.

	The mirror ``VAS Attach Rule`` doctype has been deleted.
	``ch_pos.pos_core.doctype.ch_attach_rule.ch_attach_rule.get_attach_rules_for_item``
	is the single source of truth for attach offers.
	"""
	if not item_code:
		return []
	try:
		from ch_pos.pos_core.doctype.ch_attach_rule.ch_attach_rule import (
			get_attach_rules_for_item,
		)
	except ImportError:
		return []
	return get_attach_rules_for_item(item_code)


@frappe.whitelist(methods=["POST"])
def auto_match_partner_commissions(partner=None):
	"""Simple reconciliation helper: mark rows with settlement_reference as matched."""
	require_role_setting(
		"vas_finance_roles",
		("Accounts Manager", "CH Warranty Manager"),
		action=_("reconcile VAS partner commissions"),
	)
	if not frappe.db.exists("DocType", "VAS Commission"):
		return {"updated": 0}

	filters = {
		"settlement_status": "Pending",
		"settlement_reference": ["is", "set"],
	}
	if partner:
		filters["partner"] = partner

	updated = frappe.db.count("VAS Commission", filters)
	if updated:
		frappe.db.set_value(
			"VAS Commission",
			filters,
			"settlement_status",
			"Matched",
			update_modified=False,
		)

	return {"updated": updated}
