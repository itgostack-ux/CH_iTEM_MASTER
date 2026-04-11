"""
Claim Engine — generate Scheme Claim Summary from achievement data.

Called by SupplierSchemeCircular.generate_claim() whitelisted method.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate


def generate_claim_summary(scheme_name):
	"""
	Aggregate achievement ledger data for the scheme, compute slab payouts,
	apply TDS, and create a Scheme Claim Summary document.

	Returns the name of the created Scheme Claim Summary.
	"""
	scheme = frappe.get_doc("Supplier Scheme Circular", scheme_name)

	if scheme.docstatus != 1:
		frappe.throw(_("Scheme must be submitted before generating a claim"))

	if scheme.status not in ("Active", "Closed"):
		frappe.throw(_("Scheme must be Active or Closed to generate a claim"))

	# Load nested rule details (table-in-a-child-table)
	from ch_item_master.supplier_scheme.engine import _ensure_rule_details
	_ensure_rule_details(scheme)

	# Check for existing un-cancelled claim
	existing = frappe.db.exists(
		"Scheme Claim Summary",
		{"scheme": scheme_name, "docstatus": ("!=", 2), "claim_status": ("!=", "Draft")},
	)
	if existing:
		frappe.throw(
			_("An active claim {0} already exists for this scheme. Cancel it first.").format(existing)
		)

	# Aggregate achievement data
	agg = frappe.db.sql("""
		SELECT
			COUNT(*) as total_entries,
			SUM(qty) as total_qty,
			SUM(CASE WHEN eligible_for_slab = 1 THEN qty ELSE 0 END) as eligible_qty,
			SUM(CASE WHEN eligible_for_payout = 1 THEN computed_payout ELSE 0 END) as base_payout
		FROM `tabScheme Achievement Ledger`
		WHERE scheme = %s AND is_reversed = 0
	""", scheme_name, as_dict=True)[0]

	# Compute per-rule slab breakdown for the computation log
	rule_breakdown = _compute_rule_breakdown(scheme)
	base_payout = flt(agg.base_payout)
	additional_payout = sum(r.get("additional_payout", 0) for r in rule_breakdown)
	total_payout = base_payout + additional_payout

	# TDS
	tds_percent = flt(scheme.tds_percent) if scheme.tds_applicable else 0
	tds_amount = flt(total_payout * tds_percent / 100, 2)
	net_claim = flt(total_payout - tds_amount, 2)

	# Slab description
	slab_labels = [r["slab_achieved"] for r in rule_breakdown if r.get("slab_achieved")]
	slab_achieved = "; ".join(slab_labels) if slab_labels else "N/A"
	slab_description = "\n".join(
		f"Rule: {r['rule_name']} — {r.get('slab_achieved', 'N/A')} — ₹{r.get('payout', 0):,.2f}"
		for r in rule_breakdown
	)

	# Create the claim
	claim = frappe.new_doc("Scheme Claim Summary")
	claim.scheme = scheme_name
	claim.brand = scheme.brand
	claim.period_from = scheme.valid_from
	claim.period_to = scheme.valid_to
	claim.claim_status = "Draft"
	claim.total_qty = flt(agg.total_qty)
	claim.eligible_qty = flt(agg.eligible_qty)
	claim.slab_achieved = slab_achieved
	claim.slab_description = slab_description
	claim.base_payout = base_payout
	claim.additional_payout = additional_payout
	claim.total_payout = total_payout
	claim.tds_percent = tds_percent
	claim.tds_amount = tds_amount
	claim.net_claim = net_claim
	claim.computation_log = json.dumps({
		"generated_on": str(getdate()),
		"total_entries": cint(agg.total_entries),
		"rules": rule_breakdown,
	}, indent=2, default=str)

	claim.flags.ignore_permissions = True
	claim.insert()

	frappe.msgprint(
		_("Claim {0} created with net claim ₹{1:,.2f}").format(claim.name, net_claim),
		indicator="green",
	)

	return claim.name


def _compute_rule_breakdown(scheme):
	"""Per-rule slab computation for the audit log."""
	breakdown = []

	for rule in scheme.rules:
		slab_details = [d for d in (getattr(rule, 'details', None) or []) if not d.exclusion_flag]
		if not slab_details:
			continue

		# Get totals for this rule
		rule_agg = frappe.db.sql("""
			SELECT
				SUM(CASE WHEN eligible_for_slab = 1 THEN qty ELSE 0 END) as eligible_qty,
				SUM(CASE WHEN eligible_for_payout = 1 THEN computed_payout ELSE 0 END) as payout
			FROM `tabScheme Achievement Ledger`
			WHERE scheme = %s AND rule_idx = %s AND is_reversed = 0
		""", (scheme.name, rule.idx), as_dict=True)[0]

		eligible_qty = flt(rule_agg.eligible_qty)
		payout = flt(rule_agg.payout)

		slab_achieved = ""
		additional_payout = 0

		if rule.payout_basis == "Slab Based":
			# Find matching slab for the aggregate qty
			for d in sorted(slab_details, key=lambda x: cint(x.qty_from)):
				if cint(d.qty_from) <= eligible_qty and (cint(d.qty_to) == 0 or eligible_qty <= cint(d.qty_to)):
					slab_achieved = f"{d.qty_from}-{d.qty_to or '∞'} units"
					additional_payout = flt(d.additional_payout) * eligible_qty

		breakdown.append({
			"rule_name": rule.rule_name,
			"rule_type": rule.rule_type,
			"payout_basis": rule.payout_basis,
			"eligible_qty": eligible_qty,
			"slab_achieved": slab_achieved,
			"payout": payout,
			"additional_payout": additional_payout,
		})

	return breakdown
