# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
v19_backfill_item_mrp — backfill Item.ch_item_mrp from active CH Item Price.

Rationale:
  - ch_item_mrp is a new mandatory field for stock items.
  - Existing items won't have it set yet; this patch seeds the value from
    the best available CH Item Price so existing items don't break on next save.
  - For each stock item that has ch_item_mrp = 0 / NULL:
      1. Find Active CH Item Price with the highest mrp (most conservative ceiling)
      2. Fall back to Scheduled if no Active record
      3. If no CH Item Price at all, leave ch_item_mrp NULL (not forced to 0) so
         the next human save still triggers the mandatory validation.
  - Idempotent: only updates items where ch_item_mrp is falsy.
"""

import frappe


def execute():
	# Guard: if the column doesn't exist yet (e.g. migrate is still mid-flight),
	# skip gracefully — the next migrate run will succeed.
	col_exists = frappe.db.sql(
		"SHOW COLUMNS FROM `tabItem` LIKE 'ch_item_mrp'"
	)
	if not col_exists:
		print("[v19_backfill_item_mrp] Column ch_item_mrp not yet installed — skipping. Re-run migrate.")
		return

	# Fetch all stock items with no MRP set yet
	items = frappe.db.sql(
		"""
		SELECT name, item_code, ch_item_mrp
		FROM `tabItem`
		WHERE is_stock_item = 1 AND disabled = 0
		""",
		as_dict=True,
	)

	updated = 0
	skipped = 0

	for item in items:
		if item.ch_item_mrp:
			skipped += 1
			continue  # already set — idempotent, nothing to do

		item_code = item.item_code or item.name

		# Pick the highest MRP from Active prices first, then Scheduled
		mrp = _best_mrp(item_code, "Active") or _best_mrp(item_code, "Scheduled")

		if not mrp:
			skipped += 1
			continue  # no price data; leave NULL so mandatory validation fires on next save

		frappe.db.set_value("Item", item.name, "ch_item_mrp", mrp, update_modified=False)
		updated += 1

	frappe.db.commit()
	print(f"[v19_backfill_item_mrp] Updated {updated} items, skipped {skipped}.")


def _best_mrp(item_code: str, status: str):
	"""Return the highest MRP from CH Item Price for the given item and status."""
	result = frappe.db.sql(
		"""
		SELECT MAX(mrp)
		FROM `tabCH Item Price`
		WHERE item_code = %s AND status = %s AND mrp > 0
		""",
		(item_code, status),
	)
	return result[0][0] if result and result[0][0] else None
