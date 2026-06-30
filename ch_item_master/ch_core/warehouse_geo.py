"""Warehouse geographic hierarchy helpers.

Implements the SAP/Oracle-style 4-level retail warehouse tree under the
ERPNext company root:

    All Warehouses - <ABBR>
    └── <City> - <ABBR>             (is_group=1, ch_location_type='City Group')
        └── <Zone> - <ABBR>         (is_group=1, ch_location_type='Zone Group')
            ├── <Zone> Hub - <ABBR> (is_group=0, ch_location_type='Zone Warehouse')
            └── <Store> - <ABBR>    (is_group=1, ch_location_type='Store Group')
                ├── <Store>-Sellable - <ABBR>  (ch_bin_type='Sellable')
                ├── <Store>-Damaged  - <ABBR>  (ch_bin_type='Damaged')
                ├── <Store>-Demo     - <ABBR>  (ch_bin_type='Demo')
                └── <Store>-Buyback  - <ABBR>  (ch_bin_type='Buyback')

Mappings to enterprise ERP terms:
    City Group   ~ SAP Region          / Oracle Business Unit
    Zone Group   ~ SAP Plant           / Oracle Inventory Org
    Store Group  ~ SAP Storage Loc.    / Oracle Subinventory / D365 Location
    Bin (leaf)   ~ SAP Storage Bin     / Oracle Locator      / D365 License Plate

All ``ensure_*`` helpers are idempotent and safe to call from before_save,
after_insert, after_migrate, or one-shot patches.
"""

from __future__ import annotations

import frappe
from frappe.model.rename_doc import rename_doc as _rename_doc


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def _company_abbr(company: str) -> str:
	return frappe.db.get_value("Company", company, "abbr") or ""


def _suffix(company: str) -> str:
	abbr = _company_abbr(company)
	return f" - {abbr}" if abbr else ""


def _city_group_name(company: str, city: str) -> str:
	city_label = frappe.db.get_value("CH City", city, "city_name") or city
	return f"{city_label}{_suffix(company)}"


def _zone_group_name(company: str, zone: str) -> str:
	zone_label = frappe.db.get_value("CH Store Zone", zone, "zone_name") or zone
	return f"{zone_label}{_suffix(company)}"


def _store_group_name(company: str, store_code: str) -> str:
	return f"{store_code}{_suffix(company)}"


def _sellable_leaf_name(company: str, store_code: str) -> str:
	return f"{store_code}-Sellable{_suffix(company)}"


# ---------------------------------------------------------------------------
# Warehouse ensure helpers
# ---------------------------------------------------------------------------

def _company_root(company: str) -> str | None:
	"""Return the ERPNext default company root warehouse name, if any.

	ERPNext seeds every company with a top-level group warehouse called
	``All Warehouses - <ABBR>``. Some sites have additional root-level
	groups (legacy hubs, accidental top-level warehouses, etc.) — those
	must NEVER be treated as "the root" because everything we attach
	would end up under a stray hub instead of the canonical root.
	"""
	if not company:
		return None
	abbr = _company_abbr(company)
	canonical = f"All Warehouses{(' - ' + abbr) if abbr else ''}"
	# 1) Prefer the canonical ERPNext root if it exists.
	if frappe.db.exists("Warehouse", canonical):
		return canonical
	# 2) Otherwise fall back to any root-level group warehouse for the company.
	root = frappe.db.get_value(
		"Warehouse",
		{"company": company, "is_group": 1, "parent_warehouse": ["in", [None, ""]]},
		"name",
	)
	if root:
		return root
	# 3) Last resort: return the canonical name even if it doesn't exist yet;
	#    callers that try to reparent under it will fail loudly.
	return canonical


def _is_company_root(name: str) -> bool:
	"""True if `name` is an ERPNext company-root warehouse (no parent).

	The root must NEVER be reparented or have its ch_location_type rewritten.
	"""
	if not name:
		return False
	if name.startswith("All Warehouses"):
		return True
	info = frappe.db.get_value(
		"Warehouse", name, ["is_group", "parent_warehouse"], as_dict=True
	)
	return bool(info and info.is_group and not (info.parent_warehouse or ""))


def _is_descendant(candidate_parent: str, node: str, max_hops: int = 64) -> bool:
	"""True if `candidate_parent` is `node` itself or a descendant of `node`.

	Used to refuse reparent operations that would create a cycle.
	"""
	if not candidate_parent or not node:
		return False
	cursor = candidate_parent
	for _ in range(max_hops):
		if cursor == node:
			return True
		parent = frappe.db.get_value("Warehouse", cursor, "parent_warehouse")
		if not parent or parent == cursor:
			return False
		cursor = parent
	return False


def _ensure_group(name: str, *, company: str, parent: str | None, location_type: str,
				  city: str | None = None, zone: str | None = None) -> str:
	"""Create or update a group warehouse with the given name. Returns name.

	Safety rails:
	  * The ERPNext company-root warehouse is NEVER reparented or restamped.
	  * A reparent that would introduce a cycle is silently rejected.
	"""
	# Don't ever touch the company root warehouse.
	if _is_company_root(name):
		return name

	if frappe.db.exists("Warehouse", name):
		updates = {
			"ch_city": city or None,
			"ch_zone": zone or None,
			"ch_location_type": location_type,
		}
		if parent and not _is_descendant(parent, name):
			current_parent = frappe.db.get_value("Warehouse", name, "parent_warehouse")
			if current_parent != parent:
				updates["parent_warehouse"] = parent
		frappe.db.set_value("Warehouse", name, updates, update_modified=False)
		return name

	# Derive the base warehouse_name (without the " - <ABBR>" suffix) so
	# ERPNext autoname matches the deterministic name we expect.
	abbr = _company_abbr(company)
	base_name = name[: -len(f" - {abbr}")] if abbr and name.endswith(f" - {abbr}") else name

	wh = frappe.new_doc("Warehouse")
	wh.warehouse_name = base_name
	wh.company = company
	wh.is_group = 1
	if parent:
		wh.parent_warehouse = parent
	wh.ch_city = city or None
	wh.ch_zone = zone or None
	wh.ch_location_type = location_type
	wh.flags.ignore_permissions = True
	wh.insert(ignore_permissions=True)
	return wh.name


def ensure_city_group(company: str, city: str) -> str | None:
	"""Ensure a `<City> - <ABBR>` group warehouse exists at the company root."""
	if not (company and city):
		return None
	root = _company_root(company)
	name = _city_group_name(company, city)
	return _ensure_group(
		name,
		company=company,
		parent=root,
		location_type="City Group",
		city=city,
	)


def ensure_zone_group(company: str, zone: str) -> str | None:
	"""Ensure a `<Zone> - <ABBR>` group warehouse exists under its city group."""
	if not (company and zone):
		return None
	z = frappe.db.get_value(
		"CH Store Zone", zone, ["city", "source_warehouse"], as_dict=True
	)
	if not z:
		return None
	city_group = ensure_city_group(company, z.city) if z.city else _company_root(company)
	name = _zone_group_name(company, zone)
	zg = _ensure_group(
		name,
		company=company,
		parent=city_group,
		location_type="Zone Group",
		city=z.city,
		zone=zone,
	)
	# Reparent the existing zone hub (source_warehouse) under the zone group
	# so the legacy zone warehouse becomes a leaf of the zone group, not a
	# sibling of it.
	#
	# Safety rails:
	#   * NEVER touch the company root (would create a cycle root -> zone
	#     group -> city group -> root).
	#   * NEVER pull a CITY-LEVEL hub down into a zone group. Per the
	#     Oracle Inter-Org / SAP DC pattern, a city hub serves ALL zones in
	#     the city and must live under the City Group, not one of its
	#     children. Multiple zones may reference the same city hub as their
	#     ``source_warehouse``; reparenting would yank the hub down into
	#     whichever zone is processed last.
	if (
		z.source_warehouse
		and frappe.db.exists("Warehouse", z.source_warehouse)
		and not _is_company_root(z.source_warehouse)
		and not _is_descendant(zg, z.source_warehouse)
	):
		src_parent = frappe.db.get_value(
			"Warehouse", z.source_warehouse, "parent_warehouse"
		)
		src_parent_type = frappe.db.get_value(
			"Warehouse", src_parent, "ch_location_type"
		) if src_parent else None
		is_city_hub = src_parent_type == "City Group"
		if src_parent != zg and not is_city_hub:
			frappe.db.set_value(
				"Warehouse", z.source_warehouse, "parent_warehouse", zg,
				update_modified=False,
			)
	return zg


def ensure_store_group(store) -> str | None:
	"""Ensure a `<store_code> - <ABBR>` group warehouse exists under the zone group.

	`store` may be a CH Store name or a dict/Document with .name/.company/.zone/.city/.store_code.
	Returns the store group warehouse name (or None when prerequisites are missing).
	"""
	if isinstance(store, str):
		store = frappe.db.get_value(
			"CH Store", store,
			["name", "company", "zone", "city", "store_code"],
			as_dict=True,
		)
	if not store or not store.get("company") or not store.get("store_code"):
		return None

	parent = None
	if store.get("zone"):
		parent = ensure_zone_group(store["company"], store["zone"])
	if not parent and store.get("city"):
		parent = ensure_city_group(store["company"], store["city"])
	if not parent:
		parent = _company_root(store["company"])

	name = _store_group_name(store["company"], store["store_code"])
	return _ensure_group(
		name,
		company=store["company"],
		parent=parent,
		location_type="Store Group",
		city=store.get("city"),
		zone=store.get("zone"),
	)


# ---------------------------------------------------------------------------
# City Hub + In-Transit (Oracle Inter-Org pattern)
# ---------------------------------------------------------------------------
#
# Oracle Inventory Cloud models inter-org transfers as:
#   <source org> --(ship)--> <destination org>'s In-Transit subinventory
#                            --(receive)--> <destination org>'s default inv.
#
# We mirror that pattern at the city level: each city has a Hub (DC-style
# leaf that holds upstream inventory before fan-out) and an In-Transit
# warehouse that captures stock owned by the destination city but not yet
# received at a specific store. The company-level ``Goods In Transit -
# <abbr>`` remains as the fallback used by ``stock_entry._get_transit_
# warehouse`` when the destination has no city.


def _city_hub_name(company: str, city: str) -> str:
	city_label = frappe.db.get_value("CH City", city, "city_name") or city
	return f"{city_label} - Hub{_suffix(company)}"


def _city_transit_name(company: str, city: str) -> str:
	city_label = frappe.db.get_value("CH City", city, "city_name") or city
	return f"{city_label} - In-Transit{_suffix(company)}"


def _ensure_leaf(
	name: str,
	*,
	company: str,
	parent: str | None,
	location_type: str,
	city: str | None = None,
	zone: str | None = None,
	bin_type: str | None = None,
) -> str | None:
	"""Idempotently ensure a NON-group warehouse with the given metadata exists.

	Mirrors :func:`_ensure_group` but for leaf warehouses (hubs, transit
	warehouses, sellable bins). Refuses to touch the company root.
	"""
	if not (name and company):
		return None
	if _is_company_root(name):
		return name

	if frappe.db.exists("Warehouse", name):
		updates = {}
		if city is not None:
			updates["ch_city"] = city or None
		if zone is not None:
			updates["ch_zone"] = zone or None
		if location_type is not None:
			updates["ch_location_type"] = location_type
		if bin_type is not None:
			updates["ch_bin_type"] = bin_type
		if parent and not _is_descendant(parent, name):
			current_parent = frappe.db.get_value(
				"Warehouse", name, "parent_warehouse"
			)
			if current_parent != parent:
				updates["parent_warehouse"] = parent
		# Defensive: a leaf cannot also be a group.
		if frappe.db.get_value("Warehouse", name, "is_group"):
			updates["is_group"] = 0
		if updates:
			frappe.db.set_value("Warehouse", name, updates, update_modified=False)
		return name

	abbr = _company_abbr(company)
	base_name = name[: -len(f" - {abbr}")] if abbr and name.endswith(f" - {abbr}") else name

	wh = frappe.new_doc("Warehouse")
	wh.warehouse_name = base_name
	wh.company = company
	wh.is_group = 0
	if parent:
		wh.parent_warehouse = parent
	if city:
		wh.ch_city = city
	if zone:
		wh.ch_zone = zone
	if location_type:
		wh.ch_location_type = location_type
	if bin_type:
		wh.ch_bin_type = bin_type
	wh.flags.ignore_permissions = True
	wh.insert(ignore_permissions=True)
	return wh.name


def ensure_city_hub(company: str, city: str) -> str | None:
	"""Ensure ``<City> - Hub - <ABBR>`` Zone Warehouse leaf exists under the City Group.

	The Hub is the DC-style anchor that holds city-level inventory before
	fan-out to stores. Oracle Inventory Cloud calls this an "Inventory Org
	with subinventories"; SAP calls it a Distribution Center. We model it
	as a leaf so it can post Stock Ledger Entries directly.
	"""
	if not (company and city):
		return None
	city_group = ensure_city_group(company, city)
	return _ensure_leaf(
		_city_hub_name(company, city),
		company=company,
		parent=city_group,
		location_type="Zone Warehouse",
		city=city,
	)


def ensure_city_transit(company: str, city: str) -> str | None:
	"""Ensure ``<City> - In-Transit - <ABBR>`` Transit Warehouse leaf under the City Group.

	Mirrors Oracle's per-receiving-org in-transit subinventory: goods shipped
	from another city land here until the destination store posts receipt.
	The company-level ``Goods In Transit - <abbr>`` is preserved as the
	final fallback for transfers without a known destination city.
	"""
	if not (company and city):
		return None
	city_group = ensure_city_group(company, city)
	return _ensure_leaf(
		_city_transit_name(company, city),
		company=company,
		parent=city_group,
		location_type="Transit Warehouse",
		city=city,
	)


# ---------------------------------------------------------------------------
# Per-store hierarchy migration
# ---------------------------------------------------------------------------

def restructure_store_tree(store_name: str) -> dict:
	"""Migrate one CH Store from the flat sibling-bin layout to the
	group-rooted layout described at the top of this module.

	Steps (all idempotent):

	  1. Resolve city, zone, store_group warehouses (create if missing).
	  2. If the store's current ``warehouse`` is a LEAF and is NOT already
	     named ``<store_code>-Sellable``, rename it to that. ``frappe.rename_doc``
	     cascades to all Link references (Bin, SLE, Stock Entry items, Sales
	     Order items, CH Store.warehouse, etc.) — no SLE replay needed.
	  3. Reparent the Sellable leaf + Damaged / Demo / Buyback bin leaves
	     under the new store group.
	  4. Stamp ch_store.warehouse_group on the CH Store record.

	Returns a small dict summarising what changed (for logging).
	"""
	result = {
		"store": store_name,
		"renamed_sellable_from": None,
		"renamed_sellable_to": None,
		"store_group": None,
		"reparented": [],
		"skipped": None,
	}

	store = frappe.db.get_value(
		"CH Store", store_name,
		["name", "company", "zone", "city", "store_code", "warehouse", "warehouse_group"],
		as_dict=True,
	)
	if not store:
		result["skipped"] = "store_not_found"
		return result
	if not store.warehouse or not store.company or not store.store_code:
		result["skipped"] = "missing_warehouse_or_code"
		return result

	# 1. Build the geographic chain and create the store group.
	store_group = ensure_store_group(store)
	if not store_group:
		result["skipped"] = "cannot_resolve_parent"
		return result
	result["store_group"] = store_group

	# 2. Optionally rename the existing leaf to <store_code>-Sellable so it
	#    becomes a clearly-labelled bin alongside Damaged/Demo/Buyback.
	current_leaf = store.warehouse
	target_leaf = _sellable_leaf_name(store.company, store.store_code)

	if current_leaf == store_group:
		# Pathological pre-existing state: the field points at a group.
		# Don't touch — operator must fix manually.
		result["skipped"] = "warehouse_points_to_group"
		return result

	leaf_info = frappe.db.get_value(
		"Warehouse", current_leaf,
		["is_group", "ch_bin_type"], as_dict=True,
	)
	if not leaf_info:
		result["skipped"] = "leaf_missing"
		return result

	if leaf_info.is_group:
		# We've been given a GROUP as the "default warehouse" — unusual but
		# possible (e.g. legacy zone-hub assignments shared across stores).
		# DO NOT stamp ch_store / ch_bin_type / parent_warehouse on a shared
		# group, that corrupts unrelated bins. Just reparent the sibling
		# bin leaves (which legitimately belong to this store) under the
		# new store group, then return.
		result["skipped"] = "default_warehouse_is_group"
		siblings = frappe.get_all(
			"Warehouse",
			filters={"ch_store": store.name, "is_group": 0},
			fields=["name", "parent_warehouse"],
		)
		for sib in siblings:
			if sib.parent_warehouse != store_group:
				frappe.db.set_value(
					"Warehouse", sib.name, "parent_warehouse", store_group,
					update_modified=False,
				)
			result["reparented"].append(sib.name)
		if store.warehouse_group != store_group:
			frappe.db.set_value(
				"CH Store", store.name, "warehouse_group", store_group,
				update_modified=False,
			)
		return result
	elif current_leaf != target_leaf:
		# Make sure the destination name isn't already taken by something else.
		if frappe.db.exists("Warehouse", target_leaf):
			# Already exists — most likely a previous (partial) run. Reuse it
			# as the canonical Sellable leaf and leave the current one alone.
			result["skipped"] = f"target_leaf_already_exists:{target_leaf}"
		else:
			# NOTE: ``frappe.rename_doc`` (top-level) is a thin whitelisted
			# wrapper that does NOT accept ``ignore_permissions``. Call the
			# internal model helper directly so we can rename even when the
			# running user lacks an explicit Warehouse write permission row.
			_rename_doc(
				"Warehouse", current_leaf, target_leaf,
				force=False, merge=False,
				ignore_permissions=True, show_alert=False,
			)
			# rename_doc cascades CH Store.warehouse via the Link field, so
			# `store.warehouse` is now `target_leaf`.
			current_leaf = target_leaf
			result["renamed_sellable_from"] = leaf_info  # type: ignore[assignment]
			result["renamed_sellable_from"] = store.warehouse
			result["renamed_sellable_to"] = target_leaf

	# 3. Stamp Sellable bin metadata and reparent under the store group.
	frappe.db.set_value(
		"Warehouse", current_leaf,
		{
			"ch_store": store.name,
			"ch_bin_type": "Sellable",
			"ch_location_type": "Store Bin",
			"ch_city": store.city,
			"ch_zone": store.zone,
			"parent_warehouse": store_group,
		},
		update_modified=False,
	)
	result["reparented"].append(current_leaf)

	# 4. Reparent the other operational bins (Damaged / Demo / Buyback, and
	#    any legacy bins still hanging around) under the same store group.
	siblings = frappe.get_all(
		"Warehouse",
		filters={
			"ch_store": store.name,
			"is_group": 0,
			"name": ("!=", current_leaf),
		},
		fields=["name", "parent_warehouse"],
	)
	for sib in siblings:
		if sib.parent_warehouse != store_group:
			frappe.db.set_value(
				"Warehouse", sib.name, "parent_warehouse", store_group,
				update_modified=False,
			)
		result["reparented"].append(sib.name)

	# 5. Persist the group pointer on the store master.
	if store.warehouse_group != store_group:
		frappe.db.set_value(
			"CH Store", store.name, "warehouse_group", store_group,
			update_modified=False,
		)

	return result


def provision_store_warehouse(store_name: str) -> dict:
	"""End-to-end provisioning for a single CH Store.

	Idempotently makes sure the store has:
	  1. A Sellable leaf warehouse (``<code>-Sellable - <ABBR>``).
	  2. CH Store.warehouse pointing at that Sellable leaf.
	  3. The three sibling bins (Damaged / Demo / Buyback) under its Store Group.
	  4. The full City → Zone → Store Group chain reparented correctly.

	Safe to call for fresh stores (no warehouse) and already-provisioned ones
	(no-op or just metadata fixups).
	"""
	store = frappe.get_doc("CH Store", store_name)
	result: dict = {"store": store_name, "actions": []}
	if not store.company or not store.store_code:
		result["skipped"] = "missing_company_or_code"
		return result

	# 1. Ensure the Sellable leaf exists, creating it at the company root if
	#    needed. ``restructure_store_tree`` will reparent it under the Store
	#    Group afterwards.
	sellable = _sellable_leaf_name(store.company, store.store_code)
	if not frappe.db.exists("Warehouse", sellable):
		_ensure_leaf(
			sellable,
			company=store.company,
			parent=_company_root(store.company),
			location_type="Store Bin",
			city=store.city,
			zone=store.zone,
			bin_type="Sellable",
		)
		result["actions"].append(f"created_sellable:{sellable}")

	# 2. Point CH Store.warehouse at the Sellable leaf. Use db.set_value to
	#    avoid triggering on_update -> ensure_store_bins recursion before we
	#    are ready.
	if store.warehouse != sellable:
		frappe.db.set_value(
			"CH Store", store.name, "warehouse", sellable, update_modified=False
		)
		store.reload()
		result["actions"].append("repointed_warehouse")

	# 3. Trigger the 3 sibling bins via the canonical entry point.
	from ch_item_master.ch_core.doctype.ch_store.ch_store import ensure_store_bins
	ensure_store_bins(store)
	result["actions"].append("ensured_bins")

	# 4. Reparent under City → Zone → Store Group.
	res = restructure_store_tree(store.name)
	result["actions"].append({"restructure": res})
	return result


def restructure_all_stores() -> dict:
	"""Run :func:`restructure_store_tree` for every CH Store (idempotent)."""
	stats = {"total": 0, "migrated": 0, "skipped": 0, "errors": 0, "details": []}
	stores = frappe.get_all("CH Store", filters={"disabled": 0}, pluck="name")
	stats["total"] = len(stores)
	for name in stores:
		try:
			res = restructure_store_tree(name)
			if res.get("skipped"):
				stats["skipped"] += 1
			else:
				stats["migrated"] += 1
			stats["details"].append(res)
		except Exception:
			stats["errors"] += 1
			frappe.log_error(frappe.get_traceback(), f"restructure_store_tree: {name}")
	frappe.db.commit()
	return stats
