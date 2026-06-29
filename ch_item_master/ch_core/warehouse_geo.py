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
	# Safety: NEVER touch the company root warehouse here, even if it happens
	# to be a zone's source_warehouse (legacy data). That would create a
	# cycle (root -> zone group -> city group -> root).
	if (
		z.source_warehouse
		and frappe.db.exists("Warehouse", z.source_warehouse)
		and not _is_company_root(z.source_warehouse)
		and not _is_descendant(zg, z.source_warehouse)
	):
		current_parent = frappe.db.get_value(
			"Warehouse", z.source_warehouse, "parent_warehouse"
		)
		if current_parent != zg:
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
			frappe.rename_doc(
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
