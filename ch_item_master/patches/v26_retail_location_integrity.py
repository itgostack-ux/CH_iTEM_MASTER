"""v26: harden retail location / hub mappings.

Repairs drift introduced by older topology passes:
  * store Sellable warehouses accidentally used as zone hubs
  * shared city hubs stamped to one arbitrary zone
  * missing hub metadata on CH Store Zone.source_warehouse

Idempotent. Safe to re-run.
"""

from __future__ import annotations

from ch_item_master.ch_core.location_hierarchy import repair_retail_location_integrity


def execute():
	result = repair_retail_location_integrity()
	print(
		"v26_retail_location_integrity: "
		f"fixed={len(result.get('fixed') or [])} "
		f"warnings={len(result.get('warnings') or [])}"
	)
	for line in (result.get("fixed") or [])[:50]:
		print(f"  fixed: {line}")
	for line in (result.get("warnings") or [])[:50]:
		print(f"  warning: {line}")
