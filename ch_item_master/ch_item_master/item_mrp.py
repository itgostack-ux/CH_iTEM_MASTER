# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
Item-level MRP (Maximum Retail Price) logic.

Design:
  - Item.ch_item_mrp is the canonical MRP field for each stock item.
  - Mandatory for stock items (is_stock_item=1) — enforced in validate_item_mrp().
  - Bidirectional sync:
      Item saved  →  sync_item_mrp_to_price()  → updates active CH Item Price.mrp
      CH Item Price on_update  →  sync_price_mrp_to_item()  → updates Item.ch_item_mrp
  - Purchase ceiling: Purchase Order / Purchase Receipt item rate may not exceed
    Item.ch_item_mrp (enforced in validate_purchase_mrp_ceiling()).

Sync guard: frappe flag "ch_mrp_sync_in_progress" prevents infinite loops.
"""

import frappe
from frappe import _
from frappe.utils import flt


# ──────────────────────────────────────────────────────────────
# 1. Item validate — enforce mandatory MRP for stock items
# ──────────────────────────────────────────────────────────────

def validate_item_mrp(doc, method=None):
	"""Throw if a stock item has no MRP set.

	Non-stock items (services, consumables, etc.) are exempt.
	"""
	if not doc.is_stock_item:
		return
	if not (doc.ch_item_mrp or 0) > 0:
		frappe.throw(
			_("MRP (Maximum Retail Price) is mandatory for stock item <b>{0}</b>. "
			  "Please enter the MRP before saving.").format(doc.item_code or doc.item_name),
			title=_("MRP Required"),
		)


# ──────────────────────────────────────────────────────────────
# 2. Item on_update — push ch_item_mrp → active CH Item Price
# ──────────────────────────────────────────────────────────────

def sync_item_mrp_to_price(doc, method=None):
	"""When Item.ch_item_mrp changes, update the Active CH Item Price records.

	Only runs when:
	  - ch_item_mrp is set and > 0
	  - value actually changed (avoids pointless writes)
	  - no sync already in progress (loop guard)

	Updates ALL Active/Scheduled CH Item Price rows for this item so that
	every channel's price record reflects the new MRP ceiling.
	"""
	if frappe.flags.get("ch_mrp_sync_in_progress"):
		return

	new_mrp = flt(doc.ch_item_mrp)
	if not new_mrp:
		return

	# on_update runs after db_update, so read the pre-save document snapshot.
	old_doc = doc.get_doc_before_save()
	old_mrp = flt(old_doc.ch_item_mrp) if old_doc else 0
	if old_doc and old_mrp == new_mrp:
		return

	active_prices = frappe.get_all(
		"CH Item Price",
		filters={
			"item_code": doc.item_code,
			"status": ("in", ["Active", "Scheduled"]),
			"mrp": ("!=", new_mrp),
		},
		fields=["name", "mrp"],
	)

	if not active_prices:
		return

	frappe.flags["ch_mrp_sync_in_progress"] = True
	try:
		for price in active_prices:
			# Use db.set_value to avoid re-triggering full validate/on_update chain
			# (governance checks in _validate_price_hierarchy still apply on next
			# user-driven save; this is a controlled system sync).
			frappe.db.set_value(
				"CH Item Price",
				price.name,
				"mrp",
				new_mrp,
				update_modified=False,
			)
			frappe.logger("item_mrp").info(
				f"[MRP Sync] Item→Price: {doc.item_code} "
				f"ch_item_mrp={new_mrp} → CH Item Price {price.name} (was {price.mrp})"
			)
	finally:
		frappe.flags["ch_mrp_sync_in_progress"] = False


# ──────────────────────────────────────────────────────────────
# 3. CH Item Price on_update — push mrp → Item.ch_item_mrp
# ──────────────────────────────────────────────────────────────

def sync_price_mrp_to_item(ch_item_price_doc):
	"""Called from CHItemPrice.on_update() after a price record is saved/approved.

	Writes ch_item_price_doc.mrp back to Item.ch_item_mrp so the item card
	always shows the current MRP ceiling.

	Only runs for Active/Scheduled prices to avoid Expired drafts polluting the
	item field. Uses the loop guard to prevent ping-pong with sync_item_mrp_to_price.
	"""
	if frappe.flags.get("ch_mrp_sync_in_progress"):
		return

	new_mrp = ch_item_price_doc.mrp or 0
	if not new_mrp:
		return

	item_code = ch_item_price_doc.item_code
	current_item_mrp = frappe.db.get_value("Item", item_code, "ch_item_mrp") or 0

	if current_item_mrp == new_mrp:
		return

	frappe.flags["ch_mrp_sync_in_progress"] = True
	try:
		frappe.db.set_value("Item", item_code, "ch_item_mrp", new_mrp, update_modified=False)
		frappe.logger("item_mrp").info(
			f"[MRP Sync] Price→Item: CH Item Price {ch_item_price_doc.name} "
			f"mrp={new_mrp} → Item {item_code} (was {current_item_mrp})"
		)
	finally:
		frappe.flags["ch_mrp_sync_in_progress"] = False


# ──────────────────────────────────────────────────────────────
# 4. Purchase Order / Purchase Invoice / Purchase Receipt validate
#    — enforce MRP ceiling on purchase rate
# ──────────────────────────────────────────────────────────────

def validate_purchase_mrp_ceiling(doc, method=None):
	"""Validate that purchase item rates do not exceed Item.ch_item_mrp.

	Applies to Purchase Order, Purchase Invoice, Purchase Receipt.
	Purchase Orders are hard-blocked so buying cannot commit to a loss-making
	price. Downstream Purchase Receipts / Purchase Invoices still warn by
	default, unless doc.flags.ch_block_above_mrp is set by a stricter caller.
	"""
	violations = []
	missing_mrp = []
	hard_stop = doc.doctype == "Purchase Order" or getattr(doc.flags, "ch_block_above_mrp", False)
	for row in (doc.items or []):
		item_code = row.item_code
		if not item_code:
			continue
		item = frappe.db.get_value("Item", item_code, ["ch_item_mrp", "is_stock_item"], as_dict=True) or {}
		item_mrp = flt(item.get("ch_item_mrp"))
		if not item_mrp:
			if hard_stop and item.get("is_stock_item"):
				missing_mrp.append(
					_("Row {0}: Stock item <b>{1}</b> has no MRP configured").format(
						row.idx, item_code
					)
				)
			continue
		rate = flt(row.rate)
		if rate > item_mrp:
			violations.append(
				_("Row {0}: Item <b>{1}</b> — rate {2} exceeds MRP {3}").format(
					row.idx, item_code, frappe.format(rate, "Currency"), frappe.format(item_mrp, "Currency")
				)
			)

	if not missing_mrp and not violations:
		return

	parts = []
	if missing_mrp:
		parts.append(
			_("The following stock items are missing MRP (Maximum Retail Price). "
			  "Update the Item MRP before proceeding:<br>")
			+ "<br>".join(missing_mrp)
		)
	if violations:
		parts.append(
			_("The following items have purchase rates exceeding their MRP (Maximum Retail Price). "
			  "Reduce the purchase rate or update the Item MRP before proceeding:<br>")
			+ "<br>".join(violations)
		)
	msg = "<br><br>".join(parts)

	if hard_stop:
		frappe.throw(msg, title=_("Purchase Rate Exceeds MRP"))
	else:
		frappe.msgprint(msg, title=_("Purchase Rate Exceeds MRP"), indicator="orange")
