# Copyright (c) 2026, GoStack and contributors
"""Backfill CH Item Offer.gift_delivery and close the spin-wheel double-gift hole.

Before this patch, approving a gamified (spin wheel) Freebie offer also
created a Product-discount Pricing Rule, so the customer got the reward
free in the original cart AND again later via the redemption code.

1. gift_delivery backfill: Freebie + is_gamified=1 -> "Spin Wheel",
   other Freebies -> "Direct (In Cart)".
2. Disable every enabled Pricing Rule linked to a spin-wheel Freebie
   (direct link + cross-company copies matched by rule_description,
   the same convention _sync_additional_companies uses).
"""

import frappe


def execute():
	if not frappe.db.table_exists("CH Item Offer"):
		return

	frappe.reload_doc("ch_item_master", "doctype", "ch_item_offer")

	frappe.db.sql(
		"""
		UPDATE `tabCH Item Offer`
		   SET gift_delivery = CASE WHEN is_gamified = 1
		                            THEN 'Spin Wheel'
		                            ELSE 'Direct (In Cart)' END
		 WHERE offer_type = 'Freebie'
		   AND IFNULL(gift_delivery, '') = ''
		"""
	)

	spin_offers = frappe.get_all(
		"CH Item Offer",
		filters={"offer_type": "Freebie", "is_gamified": 1},
		fields=["name", "offer_name", "erp_pricing_rule"],
	)

	disabled = []
	for offer in spin_offers:
		# " |" anchors the exact offer name (rule_description format is
		# "CH Offer: <name> | Type: ..."), so prefix-similar offers are safe.
		targets = set(
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
			targets.add(offer.erp_pricing_rule)

		for name in targets:
			frappe.db.set_value("Pricing Rule", name, "disable", 1)
			disabled.append(f"{name} (offer {offer.name})")

	if disabled:
		print(f"v32_gift_delivery_mode: disabled {len(disabled)} instant-gift Pricing Rule(s) "
			  f"on spin-wheel offers: {', '.join(disabled)}")
