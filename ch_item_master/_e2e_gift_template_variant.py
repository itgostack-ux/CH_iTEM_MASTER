# Copyright (c) 2026, GoStack and contributors
"""E2E check — Freebie offers at variant-TEMPLATE level vs variant level.

Market-standard semantics (SAP free-goods "most specific record wins"):
  * template offer  -> applies to every variant of the template
  * variant offer   -> overrides the template offer for that variant only

Covers Direct flow (Pricing Rule priorities + real draft Sales Invoice
through ERPNext's pricing engine), POS prompt, and spin-wheel matcher.

Run inside `bench --site erpnext.local console`:

    from ch_item_master._e2e_gift_template_variant import run
    run()

Single transaction, rolled back — nothing committed.
"""

import frappe
from frappe.utils import add_to_date, now_datetime


def _find_template_with_variants():
	for tpl in frappe.get_all(
		"Item", filters={"has_variants": 1, "disabled": 0}, pluck="name", limit=20
	):
		variants = frappe.get_all(
			"Item",
			filters={"variant_of": tpl, "disabled": 0, "is_sales_item": 1},
			pluck="name",
			limit=2,
		)
		if len(variants) >= 2:
			return tpl, variants[0], variants[1]
	raise RuntimeError("No enabled template with >=2 sellable variants on this site")


def _make_offer(company, trigger, reward, delivery, tag):
	offer = frappe.get_doc({
		"doctype": "CH Item Offer",
		"company": company,
		"offer_name": f"E2E TplVar {tag} {frappe.generate_hash(length=6)}",
		"offer_type": "Freebie",
		"value_type": "Amount",
		"value": 0,
		"gift_delivery": delivery,
		"trigger_item": trigger,
		"reward_item": reward,
		"reward_qty": 1,
		"start_date": add_to_date(now_datetime(), days=-1),
		"end_date": add_to_date(now_datetime(), days=7),
	})
	offer.flags.ignore_permissions = True
	offer.insert()
	offer.approve()
	offer.reload()
	return offer


def _free_items_on_draft_invoice(company, customer, item_code):
	"""Insert a draft SI (never submitted) and return its free-item codes."""
	inv = frappe.new_doc("Sales Invoice")
	inv.company = company
	inv.customer = customer
	inv.append("items", {"item_code": item_code, "qty": 1, "rate": 50000})
	inv.flags.ignore_permissions = True
	inv.insert()
	return [r.item_code for r in inv.items if r.get("is_free_item")]


def run():
	from ch_pos.api.gift_redemption import _find_matching_gamified_offer
	from ch_pos.api.offers import check_attachment_offers

	passed, failed = [], []

	def check(label, ok, detail=""):
		(passed if ok else failed).append(label)
		print(f"{'PASS' if ok else 'FAIL'} — {label}{(' :: ' + str(detail)) if detail and not ok else ''}")

	frappe.db.savepoint("e2e_tplvar")
	try:
		template, v1, v2 = _find_template_with_variants()
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		reward_a, reward_b = frappe.get_all(
			"Item",
			filters={
				"disabled": 0, "has_variants": 0, "is_sales_item": 1,
				"variant_of": ("in", ("", None)),
				# lifecycle gate blocks non-Active items on Sales Invoice
				"ch_lifecycle_status": ("in", ("Active", "", None)),
				"name": ("not in", (template, v1, v2)),
			},
			pluck="name", limit=2,
		)
		print(f"template={template}, v1={v1}, v2={v2}")
		print(f"reward A (template offer)={reward_a}, reward B (variant offer)={reward_b}\n")

		# --- Direct flow: template offer + variant override ---------------
		tpl_offer = _make_offer(company, template, reward_a, "Direct (In Cart)", "TPL")
		var_offer = _make_offer(company, v1, reward_b, "Direct (In Cart)", "VAR")

		tpl_prio = frappe.db.get_value("Pricing Rule", tpl_offer.erp_pricing_rule, "priority")
		var_prio = frappe.db.get_value("Pricing Rule", var_offer.erp_pricing_rule, "priority")
		check("PR priority: variant offer outranks template offer",
			int(var_prio or 0) > int(tpl_prio or 0), f"tpl={tpl_prio} var={var_prio}")

		# --- POS prompt specificity ---------------------------------------
		hits_v1 = {h["offer_name"] for h in check_attachment_offers([{"item_code": v1}], company=company)}
		hits_v2 = {h["offer_name"] for h in check_attachment_offers([{"item_code": v2}], company=company)}
		hits_mixed = {h["offer_name"] for h in check_attachment_offers(
			[{"item_code": v1}, {"item_code": v2}], company=company)}

		check("POS prompt v1: variant offer only (template suppressed)",
			var_offer.name in hits_v1 and tpl_offer.name not in hits_v1, hits_v1)
		check("POS prompt v2: template offer applies",
			tpl_offer.name in hits_v2 and var_offer.name not in hits_v2, hits_v2)
		check("POS prompt v1+v2: both offers, deduped",
			hits_mixed >= {var_offer.name, tpl_offer.name}, hits_mixed)

		# --- Real pricing engine: draft SI free items ----------------------
		customer = frappe.get_all("Customer", filters={"disabled": 0}, limit=1, pluck="name")[0]
		free_v1 = _free_items_on_draft_invoice(company, customer, v1)
		free_v2 = _free_items_on_draft_invoice(company, customer, v2)
		check("draft SI with v1: free item = variant reward (B), not template's",
			reward_b in free_v1 and reward_a not in free_v1, free_v1)
		check("draft SI with v2: free item = template reward (A)",
			reward_a in free_v2 and reward_b not in free_v2, free_v2)

		# --- Spin wheel specificity ----------------------------------------
		tpl_spin = _make_offer(company, template, reward_a, "Spin Wheel", "TPL-SPIN")
		var_spin = _make_offer(company, v1, reward_b, "Spin Wheel", "VAR-SPIN")

		from types import SimpleNamespace
		inv_v1 = SimpleNamespace(company=company, items=[frappe._dict(item_code=v1)])
		inv_v2 = SimpleNamespace(company=company, items=[frappe._dict(item_code=v2)])
		m1 = _find_matching_gamified_offer(inv_v1)
		m2 = _find_matching_gamified_offer(inv_v2)
		check("spin matcher v1: variant-level spin offer wins",
			bool(m1) and m1.name == var_spin.name, m1 and m1.name)
		check("spin matcher v2: template-level spin offer applies",
			bool(m2) and m2.name == tpl_spin.name, m2 and m2.name)
		check("spin offers created no Pricing Rules",
			not frappe.get_all("Pricing Rule", filters={
				"rule_description": ("like", "CH Offer: E2E TplVar %SPIN%"), "disable": 0,
			}))

	finally:
		frappe.db.rollback(save_point="e2e_tplvar")

	print(f"\n{len(passed)} passed, {len(failed)} failed")
	if failed:
		print("FAILED:", failed)
	return not failed
