"""Supplier Scheme public API endpoints."""

import frappe
from frappe.utils import flt, cint


@frappe.whitelist()
def get_rule_achievements(scheme):
	"""Return per-rule achievement totals vs target for the achievement section on the form."""
	frappe.has_permission("Supplier Scheme Circular", "read", throw=True)

	rules = frappe.get_all(
		"Supplier Scheme Rule",
		filters={"parent": scheme, "parenttype": "Supplier Scheme Circular"},
		fields=["name", "rule_name", "target_qty"],
		order_by="idx asc",
	)
	if not rules:
		return []

	result = []
	for rule in rules:
		row = frappe.db.sql("""
			SELECT
				COALESCE(SUM(CASE WHEN is_reversed=0 AND eligible_for_slab=1 THEN qty ELSE 0 END), 0) AS achieved_qty,
				COALESCE(SUM(CASE WHEN is_reversed=0 THEN computed_payout ELSE 0 END), 0) AS estimated_payout
			FROM `tabScheme Achievement Ledger`
			WHERE scheme = %s AND rule_name = %s AND is_reversed = 0
		""", (scheme, rule.rule_name or rule.name), as_dict=True)

		r = row[0] if row else {}
		result.append({
			"rule_name": rule.rule_name or rule.name,
			"target_qty": cint(rule.target_qty),
			"achieved_qty": cint(r.get("achieved_qty", 0)),
			"estimated_payout": flt(r.get("estimated_payout", 0)),
		})

	return result
