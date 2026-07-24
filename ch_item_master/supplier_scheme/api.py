"""Supplier Scheme public API endpoints."""

import frappe
from frappe.utils import flt, cint


@frappe.whitelist()
def get_rule_achievements(scheme):
	"""Return per-rule achievement totals vs target for the achievement section on the form."""
	scheme_doc = frappe.get_doc("Supplier Scheme Circular", scheme)
	scheme_doc.check_permission("read")

	rules = frappe.get_all(
		"Supplier Scheme Rule",
		filters={"parent": scheme, "parenttype": "Supplier Scheme Circular"},
		fields=["name", "rule_name", "target_qty"],
		order_by="idx asc",
	)
	if not rules:
		return []

	rule_names = tuple({rule.rule_name or rule.name for rule in rules})
	achievement_rows = frappe.db.sql(
		"""
		SELECT
			rule_name,
			COALESCE(SUM(CASE WHEN eligible_for_slab = 1 THEN qty ELSE 0 END), 0) AS achieved_qty,
			COALESCE(SUM(computed_payout), 0) AS estimated_payout
		FROM `tabScheme Achievement Ledger`
		WHERE scheme = %(scheme)s
			AND rule_name IN %(rule_names)s
			AND is_reversed = 0
		GROUP BY rule_name
		""",
		{"scheme": scheme, "rule_names": rule_names},
		as_dict=True,
	)
	achievement_by_rule = {row.rule_name: row for row in achievement_rows}

	result = []
	for rule in rules:
		r = achievement_by_rule.get(rule.rule_name or rule.name) or {}
		result.append({
			"rule_name": rule.rule_name or rule.name,
			"target_qty": cint(rule.target_qty),
			"achieved_qty": cint(r.get("achieved_qty", 0)),
			"estimated_payout": flt(r.get("estimated_payout", 0)),
		})

	return result
