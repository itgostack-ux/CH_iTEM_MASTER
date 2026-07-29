# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Mark Colour as a non-price-affecting variant spec.

The Ready Reckoner collapses variants onto one row only when they differ
*solely* in specs configured with ``affects_price = 0``. Every CH Sub Category
Spec row shipped with the default ``affects_price = 1``, including Colour — so
no two variants were ever legitimately groupable and the grid silently fell
back to grouping by ERPNext's variant template (all colours AND all storage
tiers of a model on one row, sharing one price).

Colour does not change the price of a phone or tablet; Storage / RAM / Network
do. Flipping Colour makes the grouping rule express what the business actually
does: one price row per model+storage, covering every colour.

Idempotent — only rows that still say affects_price = 1 are touched, so a site
that has deliberately re-enabled it for a sub-category is left alone on re-run.
"""

import frappe

#: Specs that never move the price on their own. Kept narrow on purpose: adding
#: a spec here silently merges price rows, so each entry is a pricing decision.
NON_PRICE_SPECS = ("Colour", "Color")


def execute():
	if not frappe.db.exists("DocType", "CH Sub Category Spec"):
		return
	if not frappe.db.has_column("CH Sub Category Spec", "affects_price"):
		return

	rows = frappe.get_all(
		"CH Sub Category Spec",
		filters={
			"spec": ("in", NON_PRICE_SPECS),
			"is_variant": 1,
			"affects_price": 1,
		},
		fields=["name", "parent", "spec"],
		limit_page_length=0,
	)
	if not rows:
		return

	for row in rows:
		frappe.db.set_value(
			"CH Sub Category Spec", row["name"], "affects_price", 0, update_modified=False
		)

	frappe.db.commit()
	print(
		f"v35: Colour marked non-price-affecting on {len(rows)} sub-category spec row(s): "
		+ ", ".join(sorted({r["parent"] for r in rows}))
	)
