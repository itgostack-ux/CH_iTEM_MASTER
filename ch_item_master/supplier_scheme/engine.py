"""
Supplier Scheme Engine — eligibility matching + slab computation.

Functions:
  process_invoice_items  — called from POS Invoice on_submit hook
  reverse_invoice_items  — called from POS Invoice on_cancel hook
  recompute_scheme       — full recompute of a scheme's achievement ledger payouts
"""

import frappe
from frappe.utils import cint, flt, getdate, nowdate


# ────────────────────────────────────────────────────────────────────
# 1. INVOICE → ACHIEVEMENT LEDGER  (called by doc_events hook)
# ────────────────────────────────────────────────────────────────────

def process_invoice_items(doc, method=None):
	"""Create Scheme Achievement Ledger entries for each matching scheme rule."""
	if doc.docstatus != 1:
		return

	# Collect item info from the invoice
	invoice_items = _extract_invoice_items(doc)
	if not invoice_items:
		return

	# Find all active schemes whose period covers the invoice date
	active_schemes = _get_active_schemes(getdate(doc.posting_date))
	if not active_schemes:
		return

	for scheme_name in active_schemes:
		scheme = frappe.get_cached_doc("Supplier Scheme Circular", scheme_name)
		for item_row in invoice_items:
			_match_and_create_entry(scheme, item_row, doc)


def reverse_invoice_items(doc, method=None):
	"""Mark achievement ledger entries as reversed when invoice is cancelled."""
	if doc.docstatus != 2:
		return

	entries = frappe.get_all(
		"Scheme Achievement Ledger",
		filters={"invoice": doc.name, "is_reversed": 0},
		pluck="name",
	)
	for entry_name in entries:
		frappe.db.set_value("Scheme Achievement Ledger", entry_name, {
			"is_reversed": 1,
			"eligible_for_slab": 0,
			"eligible_for_payout": 0,
			"computed_payout": 0,
		})


def _extract_invoice_items(doc):
	"""Extract item-level data needed for scheme matching."""
	items = []
	for row in doc.items:
		items.append(
			frappe._dict(
				item_code=row.item_code,
				item_name=row.item_name,
				item_group=row.item_group,
				brand=row.brand,
				qty=flt(row.qty),
				rate=flt(row.rate),
				serial_no=row.serial_no,
				warehouse=row.warehouse,
			)
		)
	return items


def _get_active_schemes(invoice_date):
	"""Return names of submitted schemes whose period covers the invoice date."""
	return frappe.get_all(
		"Supplier Scheme Circular",
		filters={
			"docstatus": 1,
			"status": "Active",
			"valid_from": ("<=", invoice_date),
			"valid_to": (">=", invoice_date),
		},
		pluck="name",
	)


def _match_and_create_entry(scheme, item_row, invoice_doc):
	"""Check each rule in the scheme; if the item matches, create a ledger entry."""
	for rule in scheme.rules:
		matched_detail = _match_item_to_rule(rule, item_row, invoice_doc)
		if not matched_detail:
			continue

		# Handle serial numbers: one entry per IMEI
		serials = _split_serials(item_row.serial_no)
		if serials:
			for sn in serials:
				_create_ledger_entry(
					scheme, rule, matched_detail, item_row, invoice_doc, serial_no=sn, qty=1
				)
		else:
			_create_ledger_entry(
				scheme, rule, matched_detail, item_row, invoice_doc,
				serial_no=None, qty=flt(item_row.qty),
			)


def _match_item_to_rule(rule, item_row, invoice_doc):
	"""
	Check if item_row matches any detail line of the rule.
	Returns the first matching Scheme Rule Detail or None.
	"""
	for detail in rule.details or []:
		if detail.exclusion_flag:
			continue

		# Date applicability check
		if detail.applicable_from_date and getdate(invoice_doc.posting_date) < getdate(detail.applicable_from_date):
			continue
		if detail.applicable_to_date and getdate(invoice_doc.posting_date) > getdate(detail.applicable_to_date):
			continue

		# Item matching (any specified criterion must match)
		if detail.item_code and detail.item_code != item_row.item_code:
			continue
		if detail.item_group and detail.item_group != item_row.item_group:
			continue
		if detail.model and detail.model != _get_item_model(item_row.item_code):
			continue
		if detail.series and not _series_match(detail.series, item_row.item_code):
			continue

		# At least one filter must be specified
		if not any([detail.item_code, detail.item_group, detail.model, detail.series]):
			continue

		# Brand must match the scheme brand
		if item_row.brand and scheme_brand(rule) and item_row.brand != scheme_brand(rule):
			continue

		# RRP band check
		if flt(detail.rrp_from) > 0 and flt(item_row.rate) < flt(detail.rrp_from):
			continue
		if flt(detail.rrp_to) > 0 and flt(item_row.rate) > flt(detail.rrp_to):
			continue

		return detail

	return None


def scheme_brand(rule):
	"""Get the brand from the parent scheme."""
	if hasattr(rule, "_scheme_brand"):
		return rule._scheme_brand
	brand = frappe.db.get_value("Supplier Scheme Circular", rule.parent, "brand")
	rule._scheme_brand = brand
	return brand


def _get_item_model(item_code):
	"""Return ch_model of an item if set."""
	return frappe.db.get_value("Item", item_code, "ch_model")


def _series_match(rule_series, item_code):
	"""Check if item belongs to the specified series."""
	item_series = frappe.db.get_value("Item", item_code, "ch_series")
	if not item_series:
		return False
	return item_series.lower() == rule_series.lower()


def _split_serials(serial_no_str):
	"""Split multi-serial field into individual serial numbers."""
	if not serial_no_str:
		return []
	return [s.strip() for s in serial_no_str.strip().split("\n") if s.strip()]


def _create_ledger_entry(scheme, rule, detail, item_row, invoice_doc, serial_no, qty):
	"""Insert one Scheme Achievement Ledger row."""
	entry = frappe.new_doc("Scheme Achievement Ledger")
	entry.scheme = scheme.name
	entry.rule_idx = rule.idx
	entry.rule_name = rule.rule_name
	entry.invoice_type = "POS Invoice"
	entry.invoice = invoice_doc.name
	entry.invoice_date = invoice_doc.posting_date
	entry.store = item_row.warehouse
	entry.company = invoice_doc.company
	entry.item_code = item_row.item_code
	entry.item_name = item_row.item_name
	entry.model = _get_item_model(item_row.item_code)
	entry.brand = item_row.brand
	entry.serial_no = serial_no
	entry.qty = qty
	entry.price_at_sale = flt(item_row.rate)
	entry.customer = invoice_doc.customer
	entry.customer_name = invoice_doc.customer_name

	# Mark slab / payout eligibility from the rule detail
	entry.eligible_for_slab = cint(detail.include_in_slab) and not cint(detail.exclusion_flag)
	# Payout eligibility depends on compliance; controller's validate handles that
	entry.eligible_for_payout = cint(detail.eligible_for_payout)

	# Per-unit base payout from the rule detail
	entry.computed_payout = flt(detail.payout_per_unit) * flt(qty) if detail.eligible_for_payout else 0

	entry.flags.ignore_permissions = True
	entry.insert()


# ────────────────────────────────────────────────────────────────────
# 2. SLAB ENGINE — recompute payouts based on aggregate quantities
# ────────────────────────────────────────────────────────────────────

def recompute_scheme(scheme_name):
	"""
	Full recompute: determine slab level per rule based on total eligible qty,
	then update each achievement ledger entry's computed_payout.
	Returns a summary dict.
	"""
	scheme = frappe.get_doc("Supplier Scheme Circular", scheme_name)
	results = []

	for rule in scheme.rules:
		slab_details = [d for d in (rule.details or []) if not d.exclusion_flag]
		if not slab_details:
			continue

		if rule.payout_basis == "Slab Based":
			result = _recompute_slab_rule(scheme, rule, slab_details)
		elif rule.payout_basis == "Per Unit":
			result = _recompute_per_unit_rule(scheme, rule, slab_details)
		elif rule.payout_basis == "Additional Margin":
			result = _recompute_additional_margin_rule(scheme, rule, slab_details)
		else:
			continue

		results.append(result)

	# Update scheme totals
	scheme._compute_totals()
	scheme.db_set({
		"total_eligible_qty": scheme.total_eligible_qty,
		"total_claim_amount": scheme.total_claim_amount,
		"total_pending_amount": scheme.total_pending_amount,
	})

	return {"rules": results, "total_eligible_qty": scheme.total_eligible_qty}


def _recompute_slab_rule(scheme, rule, slab_details):
	"""
	Slab-Based: total eligible qty determines which slab row applies.
	ALL eligible entries get the slab's payout_per_unit (retroactive).
	"""
	# Get total eligible qty for this rule
	total_qty = frappe.db.sql("""
		SELECT IFNULL(SUM(qty), 0) FROM `tabScheme Achievement Ledger`
		WHERE scheme = %s AND rule_idx = %s AND eligible_for_slab = 1 AND is_reversed = 0
	""", (scheme.name, rule.idx))[0][0]

	total_qty = flt(total_qty)

	# Find the matching slab
	matched_slab = None
	for d in sorted(slab_details, key=lambda x: cint(x.qty_from)):
		if cint(d.qty_from) <= total_qty and (cint(d.qty_to) == 0 or total_qty <= cint(d.qty_to)):
			matched_slab = d

	payout_rate = flt(matched_slab.payout_per_unit) if matched_slab else 0
	additional = flt(matched_slab.additional_payout) if matched_slab else 0
	slab_label = ""
	if matched_slab:
		slab_label = f"{matched_slab.qty_from}-{matched_slab.qty_to or '∞'} units"

	# Update all entries for this rule
	entries = frappe.get_all(
		"Scheme Achievement Ledger",
		filters={
			"scheme": scheme.name,
			"rule_idx": rule.idx,
			"is_reversed": 0,
		},
		fields=["name", "qty", "eligible_for_payout"],
	)

	total_payout = 0
	for e in entries:
		payout = (payout_rate + additional) * flt(e.qty) if e.eligible_for_payout else 0
		frappe.db.set_value("Scheme Achievement Ledger", e.name, "computed_payout", payout)
		total_payout += payout

	return {
		"rule_name": rule.rule_name,
		"rule_type": rule.rule_type,
		"payout_basis": "Slab Based",
		"total_eligible_qty": total_qty,
		"matched_slab": slab_label,
		"payout_rate": payout_rate + additional,
		"total_payout": total_payout,
		"entries_updated": len(entries),
	}


def _recompute_per_unit_rule(scheme, rule, slab_details):
	"""Per Unit — each entry gets payout based on its matching rule detail line."""
	entries = frappe.get_all(
		"Scheme Achievement Ledger",
		filters={
			"scheme": scheme.name,
			"rule_idx": rule.idx,
			"is_reversed": 0,
		},
		fields=["name", "item_code", "qty", "price_at_sale", "eligible_for_payout"],
	)

	total_payout = 0
	for e in entries:
		if not e.eligible_for_payout:
			frappe.db.set_value("Scheme Achievement Ledger", e.name, "computed_payout", 0)
			continue

		# Find matching detail for this entry's item
		rate = 0
		for d in slab_details:
			if d.item_code and d.item_code != e.item_code:
				continue
			# RRP band check
			if flt(d.rrp_from) > 0 and flt(e.price_at_sale) < flt(d.rrp_from):
				continue
			if flt(d.rrp_to) > 0 and flt(e.price_at_sale) > flt(d.rrp_to):
				continue
			rate = flt(d.payout_per_unit) + flt(d.additional_payout)
			break

		payout = rate * flt(e.qty)
		frappe.db.set_value("Scheme Achievement Ledger", e.name, "computed_payout", payout)
		total_payout += payout

	return {
		"rule_name": rule.rule_name,
		"rule_type": rule.rule_type,
		"payout_basis": "Per Unit",
		"total_payout": total_payout,
		"entries_updated": len(entries),
	}


def _recompute_additional_margin_rule(scheme, rule, slab_details):
	"""Additional Margin — payout is based on the additional_payout field only."""
	entries = frappe.get_all(
		"Scheme Achievement Ledger",
		filters={
			"scheme": scheme.name,
			"rule_idx": rule.idx,
			"is_reversed": 0,
		},
		fields=["name", "item_code", "qty", "eligible_for_payout"],
	)

	total_payout = 0
	for e in entries:
		if not e.eligible_for_payout:
			frappe.db.set_value("Scheme Achievement Ledger", e.name, "computed_payout", 0)
			continue

		rate = 0
		for d in slab_details:
			if d.item_code and d.item_code != e.item_code:
				continue
			rate = flt(d.additional_payout)
			break

		payout = rate * flt(e.qty)
		frappe.db.set_value("Scheme Achievement Ledger", e.name, "computed_payout", payout)
		total_payout += payout

	return {
		"rule_name": rule.rule_name,
		"rule_type": rule.rule_type,
		"payout_basis": "Additional Margin",
		"total_payout": total_payout,
		"entries_updated": len(entries),
	}
