"""DEV-ONLY one-shot: build the full SAP/Oracle-style warehouse topology
for BestBuy Mobiles Pvt Ltd (GoGizmo Chennai + Madurai + Mumbai).

This patch is **not** registered in ``patches.txt``. It is hand-run by an
admin once after data wipes to provision the canonical tree:

    All Warehouses - BMPL
    ├── Chennai - BMPL          (City Group)
    │   ├── Chennai - Hub       (Zone Warehouse leaf, DC-style)
    │   ├── Chennai - In-Transit(Transit Warehouse leaf, Oracle pattern)
    │   ├── Chennai North       (Zone Group)  -> 6 store groups
    │   ├── Chennai West        (Zone Group)  -> 5 store groups
    │   ├── Chennai Central     (Zone Group)  -> 4 store groups
    │   └── Chennai South       (Zone Group)  -> 9 store groups
    ├── Madurai - BMPL
    │   ├── Madurai - Hub
    │   ├── Madurai - In-Transit
    │   └── Madurai Central     -> 2 store groups
    └── Mumbai - BMPL
        ├── Mumbai - Hub
        ├── Mumbai - In-Transit
        └── Mumbai North        -> 1 store group

Run:
    bench --site erpnext.local execute \\
      ch_item_master.patches.v16_dev_provision_full_topology.execute

Idempotent. Safe to re-run.
"""

from __future__ import annotations

import frappe

from ch_item_master.ch_core.warehouse_geo import (
	ensure_city_group,
	ensure_city_hub,
	ensure_city_transit,
	provision_store_warehouse,
)


COMPANY = "BestBuy Mobiles Pvt Ltd"

# Geographic zone assignment (operator-overridable). Reflects current
# Chennai store distribution per BMPL operations input.
ZONE_MAP = {
	"Chennai North": [
		"GG-KOLATHUR", "GG-MINJUR", "GG-OLDWASHERMANPET",
		"GG-PAPERMILLS-PERAMBUR", "GG-PERAMBUR-HIGHRD", "GG-THIRUVOTTIYUR",
	],
	"Chennai West": [
		"GG-ALWARTHIRUNAGAR", "GG-AMBATTUR", "GG-ANNANAGAR",
		"GG-ASHOKNAGAR", "GG-MOGAPPAIR",
	],
	"Chennai Central": [
		"BMPL-STORE-03", "BMPL-STORE-04", "GG-KODAMBAKKAM",
		"STO-BMPL-CHENNA-0001",
	],
	"Chennai South": [
		"BMPL-STORE-02", "GG-KELAMBAKKAM", "GG-MADIPAKKAM",
		"GG-PALAVAKKAM", "GG-PALLAVARAM", "GG-PERUNGUDI",
		"GG-TAMBARAM", "GG-TAMBARAM-SHANMUGAM", "GG-WEST-TAMBARAM",
	],
	"Madurai Central": ["GG-MADURAI", "GG-MADURAI-ANNANAGAR"],
	"Mumbai North":   ["BMPL-STORE-01"],
}

# CH Store Zones we want in the system (and the city they map to).
TARGET_ZONES = [
	("Chennai North",   "Chennai"),
	("Chennai South",   "Chennai"),
	("Chennai West",    "Chennai"),
	("Chennai Central", "Chennai"),
	("Madurai Central", "Madurai"),
	("Mumbai North",    "Mumbai"),
]

# Hard-purge list: warehouses that should not exist in the canonical topology.
# These will be force-deleted along with any Stock Entries / SLEs / Bins that
# reference them. Bins for surviving warehouses get reposted from SLE.
STALE_WAREHOUSES = [
	"Mumbai North Hub - BMPL",
	"_TierB Test - BMPL",
	"Buyback Bin - BMPL",
	"QA Anna Nagar - BMPL",
	"QA Kilpauk - BMPL",
	"QA Velachery - BMPL",
	"Demo Outlet - BMPL",
	"Chennai-Hub - BMPL",
	"Marina-Beach - BMPL",
	"GG-ALWARTHIRUNAGAR-Buyback - BMPL",
	"GG-ALWARTHIRUNAGAR-Damaged - BMPL",
	"GG-ALWARTHIRUNAGAR-Demo - BMPL",
	"STO-BMPL-CHENNA-0001-Buyback - BMPL",
	"STO-BMPL-CHENNA-0001-Damaged - BMPL",
	"STO-BMPL-CHENNA-0001-Demo - BMPL",
	"STO-BMPL-CHENNA-0001-Sellable - BMPL",
]

# CH Store Zones that should be removed (test artifacts).
STALE_ZONES = ["north"]


# ---------------------------------------------------------------------------
# Hard-delete helper
# ---------------------------------------------------------------------------

def _hard_delete_warehouses(names: list[str]) -> None:
	"""Force-purge warehouses + cascade through SE / SLE / Bin / refs."""
	existing = [n for n in names if frappe.db.exists("Warehouse", n)]
	if not existing:
		print(f"[purge] none of {len(names)} stale warehouses present, nothing to do")
		return

	print(f"[purge] hard-deleting {len(existing)} warehouses:")
	for n in existing:
		print(f"          - {n}")

	in_clause = tuple(existing)

	# Gather voucher refs from SLE for repost decision.
	voucher_rows = frappe.db.sql(
		"""SELECT DISTINCT voucher_type, voucher_no
		   FROM `tabStock Ledger Entry`
		   WHERE warehouse IN %s""",
		(in_clause,),
		as_dict=True,
	)
	from collections import defaultdict
	by_type: dict[str, set[str]] = defaultdict(set)
	for r in voucher_rows:
		by_type[r["voucher_type"]].add(r["voucher_no"])

	# Find surviving (item, warehouse) pairs that need repost after we delete SLE.
	affected: set[tuple[str, str]] = set()
	for vt, names_set in by_type.items():
		if not names_set:
			continue
		rows = frappe.db.sql(
			"""SELECT DISTINCT item_code, warehouse
			   FROM `tabStock Ledger Entry`
			   WHERE voucher_type=%s AND voucher_no IN %s""",
			(vt, tuple(names_set)),
			as_dict=True,
		)
		for r in rows:
			if r["warehouse"] not in existing:
				affected.add((r["item_code"], r["warehouse"]))

	# Delete dependent vouchers. Only Stock Entry is hard-deleted here; for
	# other voucher types we just drop the SLE rows referring to doomed
	# warehouses (the parent voucher may still be needed for accounting).
	stock_entries = by_type.get("Stock Entry", set())
	if stock_entries:
		frappe.db.sql(
			"DELETE FROM `tabStock Entry Detail` WHERE parent IN %s",
			(tuple(stock_entries),),
		)
		frappe.db.sql(
			"DELETE FROM `tabStock Entry` WHERE name IN %s",
			(tuple(stock_entries),),
		)
		frappe.db.sql(
			"DELETE FROM `tabStock Ledger Entry` "
			"WHERE voucher_type='Stock Entry' AND voucher_no IN %s",
			(tuple(stock_entries),),
		)

	# Drop any remaining SLE rows that reference the doomed warehouses.
	frappe.db.sql(
		"DELETE FROM `tabStock Ledger Entry` WHERE warehouse IN %s",
		(in_clause,),
	)

	# Drop bins.
	frappe.db.sql("DELETE FROM `tabBin` WHERE warehouse IN %s", (in_clause,))

	# Null out CH-Store / CH-Store-Zone references so we don't FK-violate.
	frappe.db.sql(
		"UPDATE `tabCH Store` SET warehouse=NULL WHERE warehouse IN %s",
		(in_clause,),
	)
	frappe.db.sql(
		"UPDATE `tabCH Store` SET warehouse_group=NULL WHERE warehouse_group IN %s",
		(in_clause,),
	)
	frappe.db.sql(
		"UPDATE `tabCH Store Zone` SET source_warehouse=NULL "
		"WHERE source_warehouse IN %s",
		(in_clause,),
	)

	# Orphan any children whose parent is being deleted; they'll get
	# reparented by the provisioning pass below.
	frappe.db.sql(
		"UPDATE `tabWarehouse` SET parent_warehouse=NULL "
		"WHERE parent_warehouse IN %s",
		(in_clause,),
	)

	# Finally drop the warehouses themselves.
	frappe.db.sql("DELETE FROM `tabWarehouse` WHERE name IN %s", (in_clause,))

	# Repost surviving bins so quantities reflect the deleted SLE.
	for item, wh in affected:
		qty = frappe.db.sql(
			"""SELECT IFNULL(SUM(actual_qty), 0)
			   FROM `tabStock Ledger Entry`
			   WHERE item_code=%s AND warehouse=%s""",
			(item, wh),
		)[0][0]
		frappe.db.sql(
			"UPDATE `tabBin` SET actual_qty=%s WHERE item_code=%s AND warehouse=%s",
			(qty, item, wh),
		)

	print(
		f"[purge] done: {len(existing)} warehouses, "
		f"{sum(len(v) for v in by_type.values())} vouchers; "
		f"reposted {len(affected)} surviving bins"
	)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def execute() -> None:
	print(f"=== Dev provisioning for {COMPANY} ===")

	# 1. Wipe stale warehouses.
	print("\n[1/6] Purging stale warehouses")
	_hard_delete_warehouses(STALE_WAREHOUSES)

	# 2. Re-enable Marina Beach as the 24th Chennai store.
	if frappe.db.exists("CH Store", "STO-BMPL-CHENNA-0001"):
		frappe.db.set_value(
			"CH Store", "STO-BMPL-CHENNA-0001",
			{"disabled": 0, "warehouse": None, "warehouse_group": None},
			update_modified=False,
		)
		print("[2/6] Re-enabled CH Store STO-BMPL-CHENNA-0001 (Marina Beach)")
	else:
		print("[2/6] STO-BMPL-CHENNA-0001 not found, skipping")

	# 3. Provision City Hubs + In-Transit FIRST (so zones can use the hub as
	#    their mandatory ``source_warehouse``).
	print("\n[3/6] Provisioning city hubs and in-transit warehouses")
	city_hub: dict[str, str] = {}
	for city in ("Chennai", "Madurai", "Mumbai"):
		ensure_city_group(COMPANY, city)
		hub = ensure_city_hub(COMPANY, city)
		transit = ensure_city_transit(COMPANY, city)
		city_hub[city] = hub
		print(f"  {city}: hub={hub} | transit={transit}")
	frappe.db.commit()

	# 3b. Defensive cleanup: prior runs (before the ensure_zone_group fix
	#     that preserves city-level hubs) may have yanked the hub down into
	#     a zone group. Force-reparent any city hub back to its City Group.
	from ch_item_master.ch_core.warehouse_geo import _city_group_name
	for city, hub in city_hub.items():
		if not hub:
			continue
		expected_parent = _city_group_name(COMPANY, city)
		current_parent = frappe.db.get_value("Warehouse", hub, "parent_warehouse")
		if expected_parent and current_parent != expected_parent and \
				frappe.db.exists("Warehouse", expected_parent):
			frappe.db.set_value(
				"Warehouse", hub, "parent_warehouse", expected_parent,
				update_modified=False,
			)
			print(f"  reparented {hub}: {current_parent} -> {expected_parent}")
	frappe.db.commit()

	# 4. Ensure target zones + drop stale zones.
	print("\n[4/6] Ensuring CH Store Zones")
	for zone, city in TARGET_ZONES:
		hub = city_hub.get(city)
		if not frappe.db.exists("CH Store Zone", zone):
			doc = frappe.new_doc("CH Store Zone")
			doc.zone_name = zone
			doc.city = city
			if hub:
				doc.source_warehouse = hub
			doc.insert(ignore_permissions=True)
			print(f"  created zone: {zone} ({city}, source={hub})")
		else:
			updates: dict = {}
			cur_city = frappe.db.get_value("CH Store Zone", zone, "city")
			if cur_city != city:
				updates["city"] = city
			cur_src = frappe.db.get_value("CH Store Zone", zone, "source_warehouse")
			if hub and (not cur_src or not frappe.db.exists("Warehouse", cur_src)):
				updates["source_warehouse"] = hub
			if updates:
				frappe.db.set_value(
					"CH Store Zone", zone, updates, update_modified=False
				)
				print(f"  updated zone {zone}: {updates}")

	for stale in STALE_ZONES:
		if frappe.db.exists("CH Store Zone", stale):
			n = frappe.db.count("CH Store", {"zone": stale})
			if n == 0:
				frappe.delete_doc(
					"CH Store Zone", stale, ignore_permissions=True, force=True
				)
				print(f"  deleted stale zone: {stale}")
			else:
				print(f"  zone {stale} still has {n} stores, NOT deleting")

	# 5. Re-assign each store to its target zone.
	print("\n[5/6] Re-assigning CH Store -> zone")
	assignments = {
		code: zone for zone, codes in ZONE_MAP.items() for code in codes
	}
	for store_name, target_zone in assignments.items():
		if not frappe.db.exists("CH Store", store_name):
			print(f"  SKIP: CH Store {store_name} not found")
			continue
		current = frappe.db.get_value("CH Store", store_name, "zone")
		if current != target_zone:
			frappe.db.set_value(
				"CH Store", store_name, "zone", target_zone,
				update_modified=False,
			)
			print(f"  {store_name}: zone {current} -> {target_zone}")

	frappe.db.commit()

	# 6. Provision each enabled store's warehouse hierarchy.
	print("\n[6/6] Provisioning per-store warehouse hierarchies")
	stores = frappe.get_all(
		"CH Store",
		filters={"disabled": 0, "company": COMPANY},
		pluck="name",
		order_by="name",
	)
	stats = {"ok": 0, "skipped": 0, "errors": 0}
	for store_name in stores:
		try:
			res = provision_store_warehouse(store_name)
			if res.get("skipped"):
				stats["skipped"] += 1
				print(f"  {store_name}: SKIP - {res['skipped']}")
			else:
				stats["ok"] += 1
				actions = [a for a in res["actions"] if isinstance(a, str)]
				print(f"  {store_name}: OK ({', '.join(actions) or 'no-op'})")
		except Exception as e:
			stats["errors"] += 1
			print(f"  {store_name}: ERROR - {e}")
			frappe.log_error(
				frappe.get_traceback(), f"provision_store: {store_name}"
			)

	frappe.db.commit()
	print(f"\n=== Summary: {stats} ===")

	# 7. Cosmetic sweep: align per-bin ch_city / ch_zone to the owning store's
	#    current city/zone. Bins created before the zone reassignment retain
	#    stale stamps that confuse reports filtered on those fields.
	print("\n[7/7] Syncing per-bin ch_city / ch_zone metadata to CH Store")
	fixed = 0
	for store_name in stores:
		st = frappe.db.get_value(
			"CH Store", store_name, ["city", "zone"], as_dict=True
		)
		if not st:
			continue
		bin_rows = frappe.db.sql(
			"""SELECT name, ch_city, ch_zone
			   FROM `tabWarehouse`
			   WHERE ch_store=%s AND company=%s""",
			(store_name, COMPANY),
			as_dict=True,
		)
		for row in bin_rows:
			updates = {}
			if st.city and row["ch_city"] != st.city:
				updates["ch_city"] = st.city
			if st.zone and row["ch_zone"] != st.zone:
				updates["ch_zone"] = st.zone
			if updates:
				frappe.db.set_value(
					"Warehouse", row["name"], updates, update_modified=False
				)
				fixed += 1
	frappe.db.commit()
	print(f"  synced metadata on {fixed} warehouses")
