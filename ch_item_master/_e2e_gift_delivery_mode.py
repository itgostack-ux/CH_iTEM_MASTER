# Copyright (c) 2026, GoStack and contributors
"""E2E check — Freebie Gift Delivery split (Direct vs Spin Wheel), no double gift.

Run inside `bench --site erpnext.local console`:

    from ch_item_master._e2e_gift_delivery_mode import run
    run()

Everything happens in one transaction and is rolled back at the end —
nothing is committed.
"""

import frappe
from frappe.utils import add_to_date, now_datetime


def _pick_two_items():
	items = frappe.get_all(
		"Item",
		filters={"disabled": 0, "has_variants": 0, "is_sales_item": 1},
		fields=["name"],
		limit=2,
	)
	if len(items) < 2:
		raise RuntimeError("Need at least 2 enabled sales items on this site")
	return items[0].name, items[1].name


def _make_offer(company, trigger, reward, delivery, name_suffix):
	offer = frappe.get_doc({
		"doctype": "CH Item Offer",
		"company": company,
		"offer_name": f"E2E GiftDelivery {name_suffix} {frappe.generate_hash(length=6)}",
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


def _enabled_rules_for(offer):
	rules = set(
		frappe.get_all(
			"Pricing Rule",
			filters={
				"rule_description": ["like", f"CH Offer: {offer.offer_name} |%"],
				"disable": 0,
			},
			pluck="name",
		)
	)
	if offer.erp_pricing_rule and frappe.db.exists(
		"Pricing Rule", {"name": offer.erp_pricing_rule, "disable": 0}
	):
		rules.add(offer.erp_pricing_rule)
	return rules


def run():
	passed, failed = [], []

	def check(label, ok, detail=""):
		(passed if ok else failed).append(label)
		print(f"{'PASS' if ok else 'FAIL'} — {label}{(' :: ' + str(detail)) if detail and not ok else ''}")

	if not frappe.db.has_column("CH Item Offer", "gift_delivery"):
		raise RuntimeError(
			"gift_delivery column missing — run `bench --site <site> migrate` first"
		)

	frappe.db.savepoint("e2e_gift_delivery")
	try:
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		trigger, reward = _pick_two_items()
		print(f"Using company={company}, trigger={trigger}, reward={reward}\n")

		# 1. Direct freebie -> live Product-discount Pricing Rule
		direct = _make_offer(company, trigger, reward, "Direct (In Cart)", "DIRECT")
		check("direct: is_gamified stays 0", direct.is_gamified == 0, direct.is_gamified)
		check("direct: status Active", direct.status == "Active", direct.status)
		pr_ok = bool(direct.erp_pricing_rule) and frappe.db.get_value(
			"Pricing Rule", direct.erp_pricing_rule,
			["disable", "price_or_product_discount", "free_item"], as_dict=True,
		)
		check("direct: Pricing Rule created", bool(pr_ok))
		if pr_ok:
			check("direct: rule enabled + Product + free_item",
				pr_ok.disable == 0 and pr_ok.price_or_product_discount == "Product"
				and pr_ok.free_item == reward, pr_ok)

		# 2. Spin Wheel freebie on the SAME trigger item — must coexist,
		#    must NOT create any Pricing Rule.
		spin = _make_offer(company, trigger, reward, "Spin Wheel", "SPIN")
		check("spin: coexists with direct on same trigger (no overlap error)", True)
		check("spin: is_gamified derived to 1", spin.is_gamified == 1, spin.is_gamified)
		check("spin: status Active", spin.status == "Active", spin.status)
		check("spin: NO Pricing Rule", not _enabled_rules_for(spin), _enabled_rules_for(spin))

		# 3. POS instant-gift prompt sees only the direct offer
		from ch_pos.api.offers import check_attachment_offers
		hits = check_attachment_offers([{"item_code": trigger}], company=company)
		hit_names = {h["offer_name"] for h in hits}
		check("POS prompt includes direct offer", direct.name in hit_names, hit_names)
		check("POS prompt excludes spin offer", spin.name not in hit_names, hit_names)

		# 4. Spin-wheel matcher sees only the gamified offer
		from types import SimpleNamespace

		from ch_pos.api.gift_redemption import _find_matching_gamified_offer
		# NB: frappe._dict won't do here — `.items` must be the line list,
		# not the dict method.
		fake_inv = SimpleNamespace(
			company=company, items=[frappe._dict(item_code=trigger)]
		)
		match = _find_matching_gamified_offer(fake_inv)
		check("spin matcher returns the spin offer",
			bool(match) and match.name == spin.name, match and match.name)

		# 5. Toggle after approval: Spin -> Direct creates+enables a rule,
		#    Direct -> Spin disables it again.
		spin.gift_delivery = "Direct (In Cart)"
		spin.save(ignore_permissions=True)
		spin.reload()
		check("toggle spin->direct: rule created + enabled",
			bool(_enabled_rules_for(spin)), spin.erp_pricing_rule)

		spin.gift_delivery = "Spin Wheel"
		spin.save(ignore_permissions=True)
		spin.reload()
		check("toggle direct->spin: rule disabled again",
			not _enabled_rules_for(spin), _enabled_rules_for(spin))

		# 6. Patch repairs legacy data: blank gift_delivery + live rule on a
		#    gamified offer -> backfilled + disabled.
		frappe.db.set_value("CH Item Offer", spin.name, "gift_delivery", "")
		if spin.erp_pricing_rule:
			frappe.db.set_value("Pricing Rule", spin.erp_pricing_rule, "disable", 0)
		from ch_item_master.patches.v32_gift_delivery_mode import execute as patch_execute
		patch_execute()
		check("patch: gift_delivery backfilled to Spin Wheel",
			frappe.db.get_value("CH Item Offer", spin.name, "gift_delivery") == "Spin Wheel")
		check("patch: legacy live rule disabled", not _enabled_rules_for(spin))

	finally:
		frappe.db.rollback(save_point="e2e_gift_delivery")

	print(f"\n{len(passed)} passed, {len(failed)} failed")
	if failed:
		print("FAILED:", failed)
	return not failed
