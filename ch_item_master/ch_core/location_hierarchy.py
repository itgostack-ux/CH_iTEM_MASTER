import json

import frappe
from frappe import _
from frappe.utils import cint

from ch_item_master.config import (
	get_int_setting,
	get_role_setting,
	has_role_setting,
	is_privileged_user,
	iter_all_rows,
	require_role_setting,
)
from ch_item_master.security import ensure_company_access


GROUP_LOCATION_TYPES = {"City Group", "Zone Group", "Store Group"}
LEAF_LOCATION_TYPES = {
	"Store Warehouse",
	"Zone Warehouse",
	"Transit Warehouse",
	"Service Warehouse",
	"Store Bin",
	"Hub Bin",
	"Other",
}
STORE_BIN_TYPES = {"Sellable", "Damaged", "Demo", "Buyback"}
_LOCATION_MANAGER_ROLES = ("CH Master Manager",)
_LOCATION_VIEW_ROLES = (
	"CH Master Manager",
	"Stock Manager",
	"Stock User",
	"Store Manager",
	"Store Executive",
)
_WAREHOUSE_PICKER_ROLES = (
	"CH Master Manager",
	"Stock Manager",
	"Stock User",
	"Store Manager",
	"Store Executive",
	"Accounts Manager",
	"Accounts User",
	"Sales Manager",
	"Sales User",
	"Purchase Manager",
	"Purchase User",
)
_WAREHOUSE_SEARCH_FIELDS = {"name", "warehouse_name"}


def _require_named_permission(doctype, permission_type="read", doc=None):
	if is_privileged_user():
		return
	if not frappe.has_permission(
		doctype, ptype=permission_type, doc=doc, print_logs=False
	):
		frappe.throw(
			_("You do not have {0} permission for {1}.").format(permission_type, doctype),
			frappe.PermissionError,
		)


def _check_view_permission(*doctypes):
	if not (
		has_role_setting("location_view_roles", _LOCATION_VIEW_ROLES)
		or has_role_setting("location_manager_roles", _LOCATION_MANAGER_ROLES)
	):
		roles = get_role_setting("location_view_roles", _LOCATION_VIEW_ROLES).union(
			get_role_setting("location_manager_roles", _LOCATION_MANAGER_ROLES)
		)
		frappe.throw(
			_("You do not have permission to view the location hierarchy. Required role: {0}").format(
				", ".join(sorted(roles))
			),
			frappe.PermissionError,
		)
	for doctype in doctypes:
		_require_named_permission(doctype, "read")


def _check_warehouse_picker_permission(*doctypes):
	require_role_setting(
		"warehouse_picker_roles",
		_WAREHOUSE_PICKER_ROLES,
		action=_("search report warehouses"),
	)
	for doctype in doctypes:
		_require_named_permission(doctype, "read")


def _check_master_permission(*permissions):
	require_role_setting(
		"location_manager_roles",
		_LOCATION_MANAGER_ROLES,
		action=_("manage the location hierarchy"),
	)
	for doctype, permission_type in permissions:
		_require_named_permission(doctype, permission_type)


def _get_location_scope(company=None):
	user = getattr(frappe.session, "user", None)
	if not user or user == "Guest":
		frappe.throw(_("You must be signed in."), frappe.PermissionError)
	company = _clean(company) or None
	if company:
		_require_named_permission("Company", "read")
		if not frappe.db.exists("Company", company):
			frappe.throw(_("Company {0} was not found.").format(company), frappe.DoesNotExistError)
	if is_privileged_user(user):
		return {
			"bypass": True,
			"companies": {company} if company else set(),
			"direct_companies": {company} if company else set(),
			"direct_cities": set(),
			"direct_zones": set(),
			"stores": set(),
			"warehouses": set(),
			"requested_company": company,
		}
	try:
		from ch_erp15.ch_erp15.scope import get_user_scope
	except (ImportError, ModuleNotFoundError):
		frappe.throw(_("Location scope validation is unavailable."), frappe.PermissionError)
	scope = get_user_scope(user) or {}
	if scope.get("bypass"):
		return {**scope, "requested_company": company}
	companies = set(scope.get("companies") or ())
	if not companies:
		frappe.throw(_("No company or location scope is assigned to your user."), frappe.PermissionError)
	if company and company not in companies:
		frappe.throw(_("You are not permitted to access this company."), frappe.PermissionError)
	return {**scope, "requested_company": company}


def _scope_companies(scope):
	requested = scope.get("requested_company")
	if requested:
		return {requested}
	if scope.get("bypass"):
		return None
	return set(scope.get("companies") or ())


def _company_in_scope(scope, company):
	companies = _scope_companies(scope)
	return companies is None or bool(company and company in companies)


def _city_in_scope(scope, company, city, for_write=False):
	if not _company_in_scope(scope, company):
		return False
	if scope.get("bypass") or company in set(scope.get("direct_companies") or ()):
		return True
	field = "direct_cities" if for_write else "cities"
	return bool(city and city in set(scope.get(field) or ()))


def _zone_in_scope(scope, company, city, zone, for_write=False):
	if not _company_in_scope(scope, company):
		return False
	if scope.get("bypass") or company in set(scope.get("direct_companies") or ()):
		return True
	if for_write and city in set(scope.get("direct_cities") or ()):
		return True
	field = "direct_zones" if for_write else "zones"
	return bool(zone and zone in set(scope.get(field) or ()))


def _store_in_scope(scope, store):
	if not _company_in_scope(scope, store.get("company")):
		return False
	if scope.get("bypass") or store.get("company") in set(scope.get("direct_companies") or ()):
		return True
	return store.get("name") in set(scope.get("stores") or ())


def _warehouse_in_scope(scope, warehouse):
	company = warehouse.get("company")
	if not _company_in_scope(scope, company):
		return False
	if scope.get("bypass") or company in set(scope.get("direct_companies") or ()):
		return True
	if warehouse.get("ch_city") in set(scope.get("direct_cities") or ()):
		return True
	if warehouse.get("ch_zone") in set(scope.get("direct_zones") or ()):
		return True
	if warehouse.get("ch_store") in set(scope.get("stores") or ()):
		return True
	return warehouse.get("name") in set(scope.get("warehouses") or ())


def _branch_in_scope(scope, branch):
	company = branch.get("ch_company")
	if not _company_in_scope(scope, company):
		return False
	if scope.get("bypass") or company in set(scope.get("direct_companies") or ()):
		return True
	if branch.get("ch_city") in set(scope.get("direct_cities") or ()):
		return True
	return branch.get("ch_zone") in set(scope.get("direct_zones") or ())


def _assert_company_scope(company):
	ensure_company_access(company)
	return _get_location_scope(company)


def _assert_zone_scope(zone, scope=None, permission_type="read"):
	zone_doc = frappe.get_doc("CH Store Zone", zone)
	_require_named_permission("CH Store Zone", permission_type, zone_doc)
	scope = scope or _get_location_scope(zone_doc.company)
	if not _zone_in_scope(
		scope,
		zone_doc.company,
		zone_doc.city,
		zone_doc.name,
		for_write=permission_type != "read",
	):
		frappe.throw(_("The selected zone is outside your assigned scope."), frappe.PermissionError)
	return zone_doc, scope


def _assert_store_scope(store, scope=None, permission_type="read"):
	store_doc = frappe.get_doc("CH Store", store)
	_require_named_permission("CH Store", permission_type, store_doc)
	scope = scope or _get_location_scope(store_doc.company)
	if not _store_in_scope(scope, store_doc):
		frappe.throw(_("The selected store is outside your assigned scope."), frappe.PermissionError)
	return store_doc, scope


def _assert_warehouse_scope(warehouse, scope=None, permission_type="read"):
	warehouse_doc = frappe.get_doc("Warehouse", warehouse)
	_require_named_permission("Warehouse", permission_type)
	scope = scope or _get_location_scope(warehouse_doc.company)
	if not _warehouse_in_scope(scope, warehouse_doc):
		frappe.throw(_("The selected warehouse is outside your assigned scope."), frappe.PermissionError)
	if warehouse_doc.is_group:
		frappe.throw(_("Only ledger warehouses can be assigned as locations."), frappe.ValidationError)
	return warehouse_doc, scope


def _tree_limit():
	return get_int_setting("location_tree_row_limit", 2000, minimum=1)


def _bounded_page(start, page_len):
	limit = get_int_setting("location_query_limit", 100, minimum=1)
	return max(cint(start), 0), max(1, min(cint(page_len) or 20, limit))


def _safe_warehouse_searchfield(searchfield):
	return searchfield if searchfield in _WAREHOUSE_SEARCH_FIELDS else "name"


def _warehouse_scope_condition(scope, values, alias="wh", prefix="location_scope"):
	clauses = []
	requested_company = scope.get("requested_company")
	if requested_company:
		values[f"{prefix}_company"] = requested_company
		clauses.append(f"{alias}.company = %({prefix}_company)s")
	if scope.get("bypass"):
		return " AND ".join(clauses) or "1=1"
	grants = []
	for key, field, entries in (
		("companies", "company", scope.get("direct_companies") or ()),
		("cities", "ch_city", scope.get("direct_cities") or ()),
		("zones", "ch_zone", scope.get("direct_zones") or ()),
		("stores", "ch_store", scope.get("stores") or ()),
		("warehouses", "name", scope.get("warehouses") or ()),
	):
		entries = tuple(sorted(set(entries)))
		if not entries:
			continue
		param = f"{prefix}_{key}"
		values[param] = entries
		grants.append(f"{alias}.{field} IN %({param})s")
	clauses.append("(" + " OR ".join(grants) + ")" if grants else "1=0")
	return " AND ".join(f"({clause})" for clause in clauses)


def _clean(value):
	if value is None:
		return ""
	if not isinstance(value, str):
		frappe.throw(_("Location values must be text."), frappe.ValidationError)
	return value.strip()


def _warehouse_row(warehouse):
	if not warehouse:
		return None
	return frappe.db.get_value(
		"Warehouse",
		warehouse,
		[
			"name", "warehouse_name", "company", "is_group", "disabled",
			"parent_warehouse", "warehouse_type", "ch_city", "ch_zone",
			"ch_location_type", "ch_store", "ch_bin_type", "ch_hub_bin_type",
		],
		as_dict=True,
	)


def _is_city_level_hub(warehouse):
	"""Return True when the hub is a city-level DC leaf under a City Group."""
	wh = _warehouse_row(warehouse)
	if not wh or not wh.parent_warehouse:
		return False
	parent_type = frappe.db.get_value(
		"Warehouse", wh.parent_warehouse, "ch_location_type"
	)
	return parent_type == "City Group"


def _zones_using_hub(warehouse):
	if not warehouse or not frappe.db.table_exists("CH Store Zone"):
		return []
	return frappe.get_all(
		"CH Store Zone",
		filters={"source_warehouse": warehouse},
		fields=["name", "zone_name", "company", "city"],
		order_by="name",
		limit_page_length=_tree_limit(),
	)


def _stores_using_warehouse(warehouse):
	if not warehouse or not frappe.db.table_exists("CH Store"):
		return []
	return frappe.get_all(
		"CH Store",
		filters={"warehouse": warehouse, "disabled": 0},
		fields=["name", "store_name", "company", "city", "zone"],
		order_by="name",
		limit_page_length=_tree_limit(),
	)


def _hub_zone_value(warehouse):
	"""Return the Warehouse.ch_zone value for a hub.

	CH Store Zone.source_warehouse is authoritative. If one city-level hub
	serves multiple zones, the Warehouse row cannot honestly carry a single
	ch_zone, so leave it blank and attach it to each zone at render time.
	"""
	refs = _zones_using_hub(warehouse)
	if len(refs) != 1:
		return None
	if _is_city_level_hub(warehouse):
		return None
	return refs[0].name


def _validate_hub_candidate(warehouse, *, company=None, zone=None, city=None):
	"""Validate that a warehouse can be used as CH Store Zone.source_warehouse."""
	wh = _warehouse_row(warehouse)
	if not wh:
		frappe.throw(f"Warehouse {warehouse} not found.")
	target_city = _clean(city)
	if not target_city and zone and frappe.db.exists("CH Store Zone", zone):
		target_city = _clean(frappe.db.get_value("CH Store Zone", zone, "city"))

	if int(wh.disabled or 0):
		frappe.throw(f"Warehouse {warehouse} is disabled.")
	if int(wh.is_group or 0):
		frappe.throw(
			f"Warehouse {warehouse} is a group warehouse. "
			"Zone source/hub warehouses must be ledger warehouses so stock can post."
		)
	if company and wh.company != company:
		frappe.throw(
			f"Warehouse {warehouse} belongs to company {wh.company}, not {company}."
		)
	if _clean(wh.ch_store):
		frappe.throw(
			f"Warehouse {warehouse} is linked to CH Store {wh.ch_store}; "
			"a store warehouse cannot be used as a zone hub."
		)
	if _clean(wh.ch_bin_type):
		frappe.throw(
			f"Warehouse {warehouse} is a {_clean(wh.ch_bin_type)} store bin; "
			"a bin cannot be used as a zone hub."
		)
	if _clean(wh.ch_location_type) not in {"", "Zone Warehouse"}:
		frappe.throw(
			f"Warehouse {warehouse} has Location Type {wh.ch_location_type}; "
			"zone hubs must be blank or Zone Warehouse."
		)
	if _clean(wh.warehouse_type).lower() == "transit":
		frappe.throw(f"Warehouse {warehouse} is a Transit warehouse, not a hub.")

	if target_city and _clean(wh.ch_city) and _clean(wh.ch_city) != target_city:
		frappe.throw(
			f"Warehouse {warehouse} belongs to city {wh.ch_city}; "
			f"it cannot be used as a hub for {target_city}."
		)

	parent_city = _clean(frappe.db.get_value("Warehouse", wh.parent_warehouse, "ch_city")) if wh.parent_warehouse else ""
	if target_city and parent_city and parent_city != target_city:
		frappe.throw(
			f"Warehouse {warehouse} is under city {parent_city}; "
			f"it cannot be used as a hub for {target_city}."
		)

	tagged_zone = _clean(wh.ch_zone)
	if tagged_zone and tagged_zone != zone and frappe.db.exists("CH Store Zone", tagged_zone):
		tagged = frappe.db.get_value(
			"CH Store Zone", tagged_zone, ["company", "city"], as_dict=True
		)
		if company and tagged.company and tagged.company != company:
			frappe.throw(
				f"Warehouse {warehouse} is already tagged to zone {tagged_zone} "
				f"in company {tagged.company}."
			)
		if target_city and tagged.city and tagged.city != target_city:
			frappe.throw(
				f"Warehouse {warehouse} is already tagged to zone {tagged_zone} "
				f"in city {tagged.city}; it cannot be used for {target_city}."
			)

	for ref in _zones_using_hub(warehouse):
		if zone and ref.name == zone:
			continue
		if company and ref.company and ref.company != company:
			frappe.throw(
				f"Warehouse {warehouse} already serves zone {ref.name} "
				f"in company {ref.company}."
			)
		if target_city and ref.city and ref.city != target_city:
			frappe.throw(
				f"Warehouse {warehouse} already serves zone {ref.name} "
				f"in city {ref.city}; it cannot be used for {target_city}."
			)
	return wh


def validate_zone_source_warehouse(doc, method=None):
	"""DocType validation for CH Store Zone."""
	if not doc.get("source_warehouse"):
		return
	_validate_hub_candidate(
		doc.source_warehouse,
		company=doc.get("company"),
		zone=doc.name,
		city=doc.get("city"),
	)


def validate_warehouse_location_fields(doc, method=None):
	"""Validate CH location metadata on Warehouse saves.

	This prevents the dangerous mixed states we found: a store Sellable bin
	being tagged as a Zone Warehouse, hubs carrying store ownership, or group
	nodes being used as posting leaves.
	"""
	location_type = _clean(doc.get("ch_location_type"))
	bin_type = _clean(doc.get("ch_bin_type"))

	if location_type in GROUP_LOCATION_TYPES and not int(doc.get("is_group") or 0):
		frappe.throw(f"{location_type} warehouses must be group warehouses.")
	if location_type in LEAF_LOCATION_TYPES and int(doc.get("is_group") or 0):
		frappe.throw(f"{location_type} warehouses must be ledger warehouses.")

	if bin_type and location_type != "Store Bin":
		frappe.throw("Bin Type is only valid when Location Type is Store Bin.")
	if bin_type and bin_type not in STORE_BIN_TYPES:
		frappe.throw(
			f"Unsupported store Bin Type '{bin_type}'. "
			f"Allowed values: {', '.join(sorted(STORE_BIN_TYPES))}."
		)

	if location_type == "Zone Warehouse":
		if _clean(doc.get("ch_store")):
			frappe.throw("Zone Warehouse cannot be linked to a CH Store.")
		if bin_type:
			frappe.throw("Zone Warehouse cannot carry a store Bin Type.")

	if location_type == "Store Bin":
		if not _clean(doc.get("ch_store")):
			frappe.throw("Store Bin warehouses must be linked to a CH Store.")
		if not bin_type:
			frappe.throw("Store Bin warehouses must have a Bin Type.")

	if location_type == "Hub Bin":
		if _clean(doc.get("ch_store")) or bin_type:
			frappe.throw("Hub Bin warehouses cannot be linked to a store or store bin type.")
		if not _clean(doc.get("ch_zone")):
			frappe.throw("Hub Bin warehouses must be linked to a zone.")
		if not _clean(doc.get("ch_hub_bin_type")):
			frappe.throw("Hub Bin warehouses must have a Hub Bin Label.")


def validate_store_location_contract(store):
	"""Validate that CH Store.warehouse remains a sellable store leaf."""
	if store.get("disabled"):
		return

	if store.get("zone"):
		zone = frappe.db.get_value(
			"CH Store Zone", store.zone, ["company", "city"], as_dict=True
		)
		if zone and not store.get("city"):
			store.city = zone.city

	warehouse = store.get("warehouse")
	if not warehouse:
		return

	wh = _warehouse_row(warehouse)
	if not wh:
		frappe.throw(f"Default Warehouse {warehouse} not found.")
	if int(wh.is_group or 0):
		frappe.throw(
			f"Default Warehouse {warehouse} is a group warehouse. "
			"CH Store.warehouse must be the Sellable leaf."
		)
	if wh.company != store.company:
		frappe.throw(
			f"Default Warehouse {warehouse} belongs to company {wh.company}, "
			f"not {store.company}."
		)
	if store.get("city") and _clean(wh.ch_city) and _clean(wh.ch_city) != store.city:
		frappe.throw(
			f"Default Warehouse {warehouse} belongs to city {wh.ch_city}, "
			f"not {store.city}."
		)

	if cint(store.get("is_hub")):
		# Hub / distribution-centre stores (is_hub=1, see
		# ch_erp15.hub_capacity) link the hub warehouse — a "Zone
		# Warehouse" or "Hub Bin" leaf under the city group — not a
		# retail Sellable leaf. Skip the retail-leaf contract below but
		# keep exclusivity: two stores must never share one warehouse.
		if _clean(wh.ch_store) and wh.ch_store != store.name:
			frappe.throw(
				f"Default Warehouse {warehouse} is already linked to CH Store "
				f"{wh.ch_store}. Each store must have its own warehouse."
			)
		for ref in _stores_using_warehouse(warehouse):
			if ref.name == store.name:
				continue
			frappe.throw(
				f"Default Warehouse {warehouse} already serves store {ref.name}. "
				"Each store must have its own warehouse."
			)
		return

	if frappe.db.exists("CH Store Zone", {"source_warehouse": warehouse}):
		frappe.throw(
			f"Default Warehouse {warehouse} is configured as a zone hub. "
			"Use a dedicated Sellable store warehouse instead."
		)
	if _clean(wh.ch_location_type) == "Zone Warehouse":
		frappe.throw(
			f"Default Warehouse {warehouse} is tagged as a Zone Warehouse, "
			"not a store Sellable leaf."
		)
	if _clean(wh.ch_location_type) not in {"", "Store Warehouse", "Store Bin"}:
		frappe.throw(
			f"Default Warehouse {warehouse} has Location Type {wh.ch_location_type}; "
			"store warehouses must be blank, Store Warehouse, or Store Bin."
		)
	if _clean(wh.ch_bin_type) and _clean(wh.ch_bin_type) != "Sellable":
		frappe.throw(
			f"Default Warehouse {warehouse} is a {wh.ch_bin_type} bin; "
			"CH Store.warehouse must be Sellable."
		)

	if _clean(wh.ch_store) and wh.ch_store != store.name:
		frappe.throw(
			f"Default Warehouse {warehouse} is already linked to CH Store {wh.ch_store}. "
			"Each store must have its own Sellable warehouse."
		)

	if _clean(wh.ch_zone) and wh.ch_zone != store.get("zone"):
		tagged_zone = frappe.db.get_value(
			"CH Store Zone", wh.ch_zone, ["company", "city"], as_dict=True
		)
		if tagged_zone:
			if tagged_zone.company != store.company:
				frappe.throw(
					f"Default Warehouse {warehouse} is tagged to zone {wh.ch_zone} "
					f"in company {tagged_zone.company}."
				)
			if store.get("city") and tagged_zone.city and tagged_zone.city != store.city:
				frappe.throw(
					f"Default Warehouse {warehouse} is tagged to zone {wh.ch_zone} "
					f"in city {tagged_zone.city}; it cannot be used for {store.city}."
				)

	for ref in _stores_using_warehouse(warehouse):
		if ref.name == store.name:
			continue
		frappe.throw(
			f"Default Warehouse {warehouse} already serves store {ref.name}. "
			"Each store must have its own Sellable warehouse."
		)


def sync_zone_source_warehouse_metadata(zone):
	"""Stamp the source warehouse for one zone without corrupting shared hubs."""
	if isinstance(zone, str):
		zone = frappe.db.get_value(
			"CH Store Zone",
			zone,
			["name", "company", "city", "source_warehouse"],
			as_dict=True,
		)
	if not zone or not zone.source_warehouse:
		return None

	_validate_hub_candidate(
		zone.source_warehouse,
		company=zone.company,
		zone=zone.name,
		city=zone.city,
	)
	ch_zone = _hub_zone_value(zone.source_warehouse)
	frappe.db.set_value(
		"Warehouse",
		zone.source_warehouse,
		{
			"ch_city": zone.city or None,
			"ch_zone": ch_zone,
			"ch_location_type": "Zone Warehouse",
			"ch_store": None,
			"ch_bin_type": None,
		},
		update_modified=False,
	)
	return zone.source_warehouse


def repair_retail_location_integrity(company=None):
	"""Repair hub/store mapping drift and return an audit summary.

	Idempotent. This fixes the dangerous states automatically:
	- a store Sellable leaf configured as a zone source warehouse
	- shared city hubs stamped to one zone
	- source hub metadata missing/blank

	It intentionally does not guess city/zone for incomplete stores; those
	rows are reported so an operator can classify or disable them.
	"""
	if not frappe.db.table_exists("CH Store Zone"):
		return {"fixed": [], "warnings": []}

	from ch_item_master.ch_core.warehouse_geo import ensure_city_hub, restructure_store_tree
	from ch_item_master.ch_core.doctype.ch_store.ch_store import ensure_store_bins

	fixed = []
	warnings = []
	zone_filters = {}
	if company:
		zone_filters["company"] = company

	for zone in iter_all_rows(
		"CH Store Zone",
		filters=zone_filters,
		fields=["name", "zone_name", "company", "city", "source_warehouse"],
		order_by="name",
	):
		source = zone.source_warehouse
		needs_replacement = False
		if not source or not frappe.db.exists("Warehouse", source):
			needs_replacement = True
		else:
			wh = _warehouse_row(source)
			if (
				int(wh.is_group or 0)
				or _clean(wh.ch_store)
				or _clean(wh.ch_bin_type)
				or _clean(wh.ch_location_type) not in {"", "Zone Warehouse"}
			):
				needs_replacement = True

		if needs_replacement:
			hub = ensure_city_hub(zone.company, zone.city)
			if hub:
				frappe.db.set_value(
					"CH Store Zone", zone.name, "source_warehouse", hub,
					update_modified=False,
				)
				zone.source_warehouse = hub
				fixed.append(
					f"zone {zone.name}: source_warehouse {source or '(missing)'} -> {hub}"
				)
			else:
				warnings.append(f"zone {zone.name}: could not resolve replacement hub")
				continue

		try:
			sync_zone_source_warehouse_metadata(zone)
		except Exception as exc:
			warnings.append(f"zone {zone.name}: source metadata not synced: {exc}")

	# Any store-owned warehouse that was previously mislabeled as a hub should
	# return to Store Bin/Sellable semantics.
	store_filters = {"disabled": 0}
	if company:
		store_filters["company"] = company
	for store in iter_all_rows(
		"CH Store",
		filters=store_filters,
		fields=["name", "company", "city", "zone", "warehouse"],
		order_by="name",
	):
		if not (store.city and store.zone and store.warehouse):
			warnings.append(
				f"store {store.name}: incomplete city/zone/warehouse; manual classification required"
			)
			continue
		wh = _warehouse_row(store.warehouse)
		if not wh:
			warnings.append(f"store {store.name}: warehouse {store.warehouse} not found")
			continue
		warehouse_refs = _stores_using_warehouse(store.warehouse)
		if len(warehouse_refs) > 1:
			warnings.append(
				f"store {store.name}: warehouse {store.warehouse} is shared by "
				f"{len(warehouse_refs)} stores; each store needs its own Sellable warehouse"
			)
			continue
		elif _clean(wh.ch_location_type) == "Zone Warehouse" or _clean(wh.ch_bin_type) != "Sellable":
			frappe.db.set_value(
				"Warehouse",
				store.warehouse,
				{
					"ch_city": store.city,
					"ch_zone": store.zone,
					"ch_location_type": "Store Bin",
					"ch_store": store.name,
					"ch_bin_type": "Sellable",
				},
				update_modified=False,
			)
			fixed.append(f"store {store.name}: restored Sellable bin metadata")
		try:
			ensure_store_bins(frappe.get_doc("CH Store", store.name))
			restructure_store_tree(store.name)
		except Exception as exc:
			warnings.append(f"store {store.name}: tree not synced: {exc}")

	return {"fixed": fixed, "warnings": warnings}


def ensure_city(company, city_name, state=None):
	if not city_name:
		return None

	clean_city = city_name.strip().title()
	if not clean_city:
		return None

	# Order of lookups (cheapest first, all covered):
	#   1. Composite by (state, city_name) — the intended natural key.
	#   2. Fallback by city_name alone — handles legacy rows where state is
	#      NULL/empty on the master (e.g. imported before v23 canonicalisation).
	#   3. Compute the EXPECTED PK the way ``CHCity.autoname`` would
	#      (``{city_name}-{state_code}`` when state has a code, else just
	#      city_name) and probe by primary key. Sites that ran on the older
	#      state-code-suffixed autoname still carry rows like ``Chennai-33``
	#      whose ``city_name`` field may not exactly equal ``Chennai`` any
	#      longer — that's the exact scenario that raised the pre-fix
	#      DuplicateEntryError during after_migrate.
	#   4. Insert with ignore_if_duplicate=True as a final safety net for
	#      any legacy row shape we haven't anticipated; on collision, re-probe
	#      by the expected PK and return that.
	existing = None
	if state:
		existing = frappe.db.get_value(
			"CH City",
			{"state": state, "city_name": clean_city},
			"name",
		)
	if not existing:
		existing = frappe.db.get_value("CH City", {"city_name": clean_city}, "name")

	if not existing:
		state_token = None
		if state:
			state_code = (
				frappe.db.get_value("CH State", state, "state_code") or ""
			).strip().upper()
			if state_code:
				state_token = state_code
			else:
				state_token = "".join(ch for ch in state.upper() if ch.isalnum())
		expected_pk = f"{clean_city}-{state_token}" if state_token else clean_city
		if frappe.db.exists("CH City", expected_pk):
			existing = expected_pk

	if existing:
		return existing

	city = frappe.new_doc("CH City")
	city.city_name = clean_city
	city.state = state or None
	try:
		city.insert(ignore_if_duplicate=True)
	except frappe.DuplicateEntryError:
		# Autoname produced a PK that already exists — re-resolve by the
		# expected PK (safe because CHCity.autoname is deterministic).
		return city.name
	return city.name


def backfill_location_hierarchy():
	"""Backfill Company → City → Zone links from existing CH Store data."""
	if not frappe.db.table_exists("CH City"):
		return

	stores = iter_all_rows(
		"CH Store",
		filters={"disabled": 0},
		fields=["name", "company", "city", "state", "zone", "warehouse", "branch"],
	)

	for store in stores:
		city_name = store.city
		if not city_name or not store.company:
			continue

		city = ensure_city(store.company, city_name, store.state)
		if city and store.city != city:
			frappe.db.set_value("CH Store", store.name, "city", city, update_modified=False)

		if store.zone and city:
			frappe.db.set_value("CH Store Zone", store.zone, "city", city, update_modified=False)

		if store.warehouse and frappe.db.exists("Warehouse", store.warehouse):
			warehouse_updates = {"ch_city": city, "ch_zone": store.zone or None}
			if store.city:
				warehouse_updates["city"] = city
			if store.zone:
				# Path B Phase 2: Sellable leaves are tagged 'Store Bin' (their
				# parent Store Group represents the store in the location view).
				warehouse_updates["ch_location_type"] = "Store Bin"
			frappe.db.set_value("Warehouse", store.warehouse, warehouse_updates, update_modified=False)

		if store.branch and frappe.db.exists("Branch", store.branch):
			frappe.db.set_value(
				"Branch",
				store.branch,
				{"ch_company": store.company, "ch_city": city, "ch_zone": store.zone or None},
				update_modified=False,
			)

	# Mark zone source warehouses as zone warehouses. The source mapping lives
	# on CH Store Zone; shared city hubs intentionally keep Warehouse.ch_zone
	# blank and are attached to each zone at render time.
	for zone in iter_all_rows(
		"CH Store Zone",
		fields=["name", "company", "city", "source_warehouse"],
	):
		if zone.source_warehouse and frappe.db.exists("Warehouse", zone.source_warehouse):
			try:
				sync_zone_source_warehouse_metadata(zone)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"source warehouse sync failed for zone {zone.name}",
				)


def backfill_store_bins():
	"""Ensure every active CH Store with a warehouse has its 5 stock-state bins.

	Idempotent. Safe to run repeatedly from after_migrate.
	"""
	if not frappe.db.table_exists("CH Store"):
		return
	# Skip if the bin_type custom field hasn't been created yet (first install).
	if not frappe.db.exists("Custom Field", {"dt": "Warehouse", "fieldname": "ch_bin_type"}):
		return

	from ch_item_master.ch_core.doctype.ch_store.ch_store import ensure_store_bins

	stores = iter_all_rows(
		"CH Store",
		filters={"disabled": 0, "warehouse": ["is", "set"]},
		fields=["name"],
	)
	for row in stores:
		try:
			store = frappe.get_doc("CH Store", row.name)
			ensure_store_bins(store)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"backfill_store_bins failed for {row.name}")


def backfill_zone_hubs():
	"""Ensure every CH Store Zone has a Distribution Hub warehouse.

	A "Hub" is a ledger warehouse sitting at the city/zone level that aggregates
	stock for all stores in the zone. We don't reparent existing Store
	Warehouses (that would rewrite the warehouse tree and is risky for
	in-flight stock); we only:

	  1. Create the Hub warehouse if missing (idempotent — keyed by name).
	  2. Stamp ch_location_type='Zone Warehouse' + ch_city + ch_zone on it.
	  3. Point the Zone's source_warehouse at it.

	Result: every zone has a designated central posting warehouse retail users can
	think of as the "back-end" / consolidation point, without disturbing
	stock ledgers on the leaf store warehouses.

	Idempotent. Safe to run repeatedly from after_migrate.
	"""
	if not frappe.db.table_exists("CH Store Zone"):
		return
	if not frappe.db.exists("Custom Field", {"dt": "Warehouse", "fieldname": "ch_location_type"}):
		return

	zones = iter_all_rows(
		"CH Store Zone",
		fields=["name", "zone_name", "company", "city", "source_warehouse"],
	)
	for zone in zones:
		if not zone.company:
			continue

		hub = zone.source_warehouse
		# 1. If a valid source_warehouse is already set and exists, just stamp it.
		if hub and frappe.db.exists("Warehouse", hub):
			try:
				sync_zone_source_warehouse_metadata(zone)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"backfill_zone_hubs: invalid hub for {zone.name}",
				)
			continue

		# 2. Otherwise, create one — name it after the zone (deterministic
		#    so re-runs are idempotent even after the zone-row link breaks).
		company_abbr = frappe.db.get_value("Company", zone.company, "abbr") or ""
		base_name = f"{zone.zone_name or zone.name} Hub"
		full_name = f"{base_name} - {company_abbr}" if company_abbr else base_name

		if frappe.db.exists("Warehouse", full_name):
			hub = full_name
		else:
			try:
				wh = frappe.new_doc("Warehouse")
				wh.warehouse_name = base_name
				wh.company = zone.company
				wh.is_group = 0
				wh.ch_city = zone.city
				wh.ch_zone = zone.name
				wh.ch_location_type = "Zone Warehouse"
				wh.insert()
				hub = wh.name
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"backfill_zone_hubs: failed to create hub for {zone.name}",
				)
				continue

		# 3. Wire the zone to its hub.
		frappe.db.set_value(
			"CH Store Zone", zone.name, "source_warehouse", hub,
			update_modified=False,
		)
		try:
			zone.source_warehouse = hub
			sync_zone_source_warehouse_metadata(zone)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"backfill_zone_hubs: metadata sync failed for {zone.name}",
			)


# Default Hub Bin set every zone hub starts with. Operators add extra bins
# (Sellable-02, Quarantine, Inbound-Dock-A, …) from the Location Hierarchy
# page as the facility grows — migrate only guarantees the baseline.
DEFAULT_HUB_BIN_LABELS = ("Sellable-01",)


def backfill_default_hub_bins():
	"""Ensure every zone hub carries the default Hub Bin set.

	Runs after backfill_zone_hubs (which guarantees each zone has a hub), so
	fresh seeds and legacy sites alike end up with at least one addressable
	bin under every Distribution Hub. Idempotent — create_hub_bin is keyed
	by (zone, label). Safe to run repeatedly from after_migrate.
	"""
	if not frappe.db.table_exists("CH Store Zone"):
		return
	if not frappe.db.exists("Custom Field", {"dt": "Warehouse", "fieldname": "ch_hub_bin_type"}):
		return

	# A hub can serve several zones (Chennai - Hub → Central/North/South/West),
	# and bins belong to the physical hub, not the zone — so dedupe by hub.
	# create_hub_bin's validation only accepts the zone the hub is tagged to
	# (Warehouse.ch_zone), so prefer that zone when picking the anchor.
	zones = iter_all_rows(
		"CH Store Zone",
		filters={"source_warehouse": ("is", "set")},
		fields=["name", "source_warehouse"],
		order_by="creation",
	)
	zones_by_hub: dict[str, list[str]] = {}
	for zone in zones:
		zones_by_hub.setdefault(zone.source_warehouse, []).append(zone.name)

	created = 0
	for hub, zone_names in zones_by_hub.items():
		tagged_zone = _clean(frappe.db.get_value("Warehouse", hub, "ch_zone"))
		anchor = tagged_zone if tagged_zone in zone_names else zone_names[0]
		for label in DEFAULT_HUB_BIN_LABELS:
			if frappe.db.exists(
				"Warehouse",
				{
					"ch_location_type": "Hub Bin",
					"ch_hub_bin_type": label,
					"ch_zone": ("in", zone_names),
				},
			):
				continue
			try:
				result = create_hub_bin(anchor, label)
				if result.get("created"):
					created += 1
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"backfill_default_hub_bins: {hub}/{label}",
				)
	if created:
		print(f"backfill_default_hub_bins: created {created} default hub bin(s)")


def backfill_bin_zones():
	"""Propagate ch_zone/ch_city from a store warehouse to its orphaned child bins.

	Runs after backfill_zone_hubs so all store warehouses already have ch_zone
	stamped.  For every Store Bin whose ch_zone is NULL, look up its
	parent_warehouse; if that parent has ch_zone set, copy zone + city down.

	Idempotent.  Safe to run repeatedly from after_migrate.
	"""
	if not frappe.db.table_exists("Warehouse"):
		return
	if not frappe.db.exists("Custom Field", {"dt": "Warehouse", "fieldname": "ch_zone"}):
		return

	frappe.db.sql(
		"""
		UPDATE `tabWarehouse` child
		INNER JOIN `tabWarehouse` parent ON parent.name = child.parent_warehouse
		SET child.ch_zone = parent.ch_zone,
			child.ch_city = NULLIF(parent.ch_city, '')
		WHERE child.is_group = 0
			AND (child.ch_zone IS NULL OR child.ch_zone = '')
			AND child.parent_warehouse IS NOT NULL
			AND child.parent_warehouse != ''
			AND parent.ch_zone IS NOT NULL
			AND parent.ch_zone != ''
		"""
	)



@frappe.whitelist()
def get_company_location_tree(company=None, warehouse_view="all"):
	"""Return Company → City → Zone → Warehouses / Stores / Offices.

	warehouse_view:
	- all: include all warehouses
	- location: only Store/Zone warehouses
	- operational: everything except Store/Zone warehouses
	"""
	_check_view_permission(
		"Company", "CH State", "CH City", "CH Store Zone", "CH Store", "Warehouse"
	)
	can_read_branches = is_privileged_user() or frappe.has_permission(
		"Branch", ptype="read", print_logs=False
	)
	warehouse_view = _clean(warehouse_view).lower() or "all"
	if warehouse_view not in {"all", "location", "operational"}:
		frappe.throw(_("Invalid warehouse view."), frappe.ValidationError)
	scope = _get_location_scope(company)
	allowed_companies = _scope_companies(scope)
	limit = _tree_limit()
	companies = {}
	# Load ALL cities (incl. disabled) so zones already linked to a city
	# later disabled by the admin still render with their proper display name.
	state_codes = {
		s.name: s.state_code
		for s in frappe.get_all(
			"CH State", fields=["name", "state_code"], limit_page_length=limit
		)
	}
	city_map = {}
	for c in frappe.get_all(
		"CH City", fields=["name", "city_name", "state"], limit_page_length=limit
	):
		c.state_code = state_codes.get(c.state) if c.state else None
		city_map[c.name] = c

	zone_filters = {}
	if allowed_companies is not None:
		zone_filters["company"] = (
			next(iter(allowed_companies))
			if len(allowed_companies) == 1
			else ("in", sorted(allowed_companies))
		)

	zone_rows = []
	for zone in frappe.get_all(
		"CH Store Zone",
		filters=zone_filters,
		fields=["name", "zone_name", "company", "city", "source_warehouse"],
		limit_page_length=limit,
	):
		if not _zone_in_scope(scope, zone.company, zone.city, zone.name):
			continue
		company_node = companies.setdefault(zone.company, {"company": zone.company, "cities": {}, "system_defaults": []})
		company_node.setdefault("system_defaults", [])
		city_key = zone.city or "Unassigned"
		city_ref = city_map.get(city_key)
		city_node = company_node["cities"].setdefault(
			city_key,
			{
				"city": city_key,
				"city_name": city_ref.city_name if city_ref else city_key,
				"state": city_ref.state if city_ref else None,
				"state_code": city_ref.state_code if city_ref else None,
				"zones": {},
				"hubs": [],
				"transit": [],
			},
		)
		city_node["zones"][zone.name] = {
			"zone": zone.name,
			"zone_name": zone.zone_name,
			"source_warehouse": zone.source_warehouse,
			"warehouses": [],
			"stores": [],
			"offices": [],
		}
		zone_rows.append(zone)

	# Pre-fetch CH Store labels so warehouse pills (especially Store Bins) can
	# display the friendly store name instead of the verbose warehouse code.
	# In-memory dict join — cheaper than SQL JOIN since the CH Store master is
	# tiny and we'd otherwise have to denormalise via get_all hacks.
	# We surface `store_name` (long descriptive label, e.g. "Kelambakkam Store")
	# so the JS pill can render "<ch_store> (<ch_store_name>)" on bin pills
	# whenever the short id and long label differ.
	store_labels = {
		s.name: s.store_name
		for s in frappe.get_all(
			"CH Store",
			filters={
				**({"company": zone_filters["company"]} if "company" in zone_filters else {}),
				"disabled": 0,
			},
			fields=["name", "store_name", "company"],
			limit_page_length=limit,
		)
		if _store_in_scope(scope, s)
	}

	source_hubs = {z.source_warehouse for z in zone_rows if z.source_warehouse}
	source_hub_rows = {}
	if source_hubs:
		for hub in frappe.get_all(
			"Warehouse",
			filters={"name": ["in", list(source_hubs)], "disabled": 0},
			fields=["name", "warehouse_name", "company", "ch_city", "ch_zone",
				"ch_location_type", "ch_store", "ch_bin_type", "ch_hub_bin_type",
				"parent_warehouse"],
			limit_page_length=limit,
		):
			if _warehouse_in_scope(scope, hub):
				source_hub_rows[hub.name] = hub
	for zone in zone_rows:
		if zone.source_warehouse and zone.source_warehouse not in source_hub_rows:
			company_node = companies.get(zone.company)
			city_node = company_node["cities"].get(zone.city or "Unassigned") if company_node else None
			zone_node = city_node["zones"].get(zone.name) if city_node else None
			if zone_node:
				zone_node["source_warehouse"] = None

	# ── City-level hubs ──────────────────────────────────────────
	# A physical hub (DC) commonly sources several zones, so render it ONCE
	# per city with the list of zones it supplies — not repeated inside every
	# zone. Hub Bins belong to the hub (not a zone), so they attach here too.
	city_hubs = {}          # (company, city_key) -> { hub_name: node }
	zone_hub = {}           # zone.name -> hub_name (its source_warehouse)
	zone_city = {}          # zone.name -> (company, city_key)
	for zone in zone_rows:
		zone_city[zone.name] = (zone.company, zone.city or "Unassigned")
		if not zone.source_warehouse:
			continue
		hub = source_hub_rows.get(zone.source_warehouse)
		if not hub:
			continue
		if company and hub.company != company:
			continue
		if not _warehouse_matches_view(hub, warehouse_view):
			continue
		ck = (zone.company, zone.city or "Unassigned")
		bucket = city_hubs.setdefault(ck, {})
		node = bucket.get(zone.source_warehouse)
		if node is None:
			node = {"warehouse": frappe._dict(hub), "zones_served": [], "bins": []}
			bucket[zone.source_warehouse] = node
		node["zones_served"].append({"zone": zone.name, "zone_name": zone.zone_name})
		zone_hub[zone.name] = zone.source_warehouse

	# City-level transit ("In-Transit") warehouses: system locations for the
	# hub↔store transfer pipeline. They belong to the city, not to a sales zone,
	# so they render alongside the hub — never in a zone / "Unassigned" bucket.
	city_transit = {}       # (company, city_key) -> [warehouse rows]

	for warehouse in frappe.get_all(
		"Warehouse",
		filters={
			"disabled": 0,
			"is_group": 0,
			**({"company": zone_filters["company"]} if "company" in zone_filters else {}),
		},
		fields=["name", "warehouse_name", "company", "ch_city", "ch_zone",
			"ch_location_type", "ch_store", "ch_bin_type", "ch_hub_bin_type",
			"warehouse_type", "parent_warehouse"],
		limit_page_length=limit,
	):
		if not _warehouse_in_scope(scope, warehouse):
			continue
		if (
			warehouse.name in source_hubs
			and (warehouse.ch_location_type or "").strip() == "Zone Warehouse"
		):
			continue
		if not _warehouse_matches_view(warehouse, warehouse_view):
			continue
		# Transit / In-Transit warehouses → city-level "In-Transit" section.
		if (warehouse.ch_location_type or "").strip() == "Transit Warehouse" or \
			(warehouse.warehouse_type or "").strip() == "Transit":
			ck = (warehouse.company, warehouse.ch_city or "Unassigned")
			city_transit.setdefault(ck, []).append(warehouse)
			continue
		# ERPNext-generated company defaults (Stores / WIP / Finished Goods,
		# etc.) are tagged with ch_location_type="Other" by the seed importer.
		# They have no retail role and hold no stock, so we lift them out of the
		# city / zone iteration entirely and surface them once at the company
		# level in a compact "System Warehouses" chip row (rendered above the
		# cities in the JS). This keeps them findable without polluting the
		# geographic tree with an "Unassigned" bucket.
		if (warehouse.ch_location_type or "").strip() == "Other":
			company_node = companies.setdefault(
				warehouse.company,
				{"company": warehouse.company, "cities": {}, "system_defaults": []},
			)
			company_node.setdefault("system_defaults", []).append(warehouse)
			continue
		# Hub Bins belong to the hub (rendered once at city level). Resolve the
		# owning hub via the zone the bin was created under → its source hub.
		if (warehouse.ch_location_type or "").strip() == "Hub Bin":
			hub_name = zone_hub.get(warehouse.ch_zone)
			ck = zone_city.get(warehouse.ch_zone)
			node = city_hubs.get(ck, {}).get(hub_name) if (hub_name and ck) else None
			if node is not None:
				node["bins"].append(warehouse)
				continue
			# else fall through: orphan hub bin (no resolvable hub) → zone bucket
		# Decorate with the friendly store label (None for warehouses not
		# attached to a CH Store, which is fine — JS falls back gracefully).
		warehouse["ch_store_name"] = (
			store_labels.get(warehouse.ch_store) if warehouse.ch_store else None
		)
		_zone_bucket(
			companies,
			warehouse.company,
			warehouse.ch_city,
			warehouse.ch_zone,
		)["warehouses"].append(warehouse)

	# Attach each city's hubs to its city node.
	for (comp, city_key), hubs in city_hubs.items():
		company_node = companies.get(comp)
		if not company_node:
			continue
		city_node = company_node["cities"].get(city_key)
		if city_node is not None:
			city_node["hubs"] = list(hubs.values())

	# Attach each city's transit ("In-Transit") warehouses.
	for (comp, city_key), whs in city_transit.items():
		company_node = companies.get(comp)
		if not company_node:
			continue
		city_node = company_node["cities"].get(city_key)
		if city_node is not None:
			city_node["transit"] = whs

	store_filters = {
		"disabled": 0,
		**({"company": zone_filters["company"]} if "company" in zone_filters else {}),
	}
	for store in frappe.get_all(
		"CH Store",
		filters=store_filters,
		fields=["name", "store_code", "store_name", "company", "city", "zone", "warehouse", "store_status", "opening_date"],
		limit_page_length=limit,
	):
		if not _store_in_scope(scope, store):
			continue
		_zone_bucket(companies, store.company, store.city, store.zone)["stores"].append(store)

	if can_read_branches:
		branch_filters = (
			{"ch_company": zone_filters["company"]} if "company" in zone_filters else {}
		)
		for office in frappe.get_all(
			"Branch",
			filters=branch_filters,
			fields=["name", "branch", "ch_company", "ch_city", "ch_zone"],
			limit_page_length=limit,
		):
			office_company = office.ch_company
			if not office_company or not _branch_in_scope(scope, office):
				continue
			_zone_bucket(companies, office_company, office.ch_city, office.ch_zone)["offices"].append(office)

	return _serialize_tree(companies)


def _zone_bucket(companies, company, city, zone):
	company_node = companies.setdefault(company, {"company": company, "cities": {}, "system_defaults": []})
	company_node.setdefault("system_defaults", [])
	city_key = city or "Unassigned"
	city_node = company_node["cities"].setdefault(city_key, {"city": city_key, "city_name": city_key, "state": None, "state_code": None, "zones": {}, "hubs": [], "transit": []})
	zone_key = zone or "Unassigned"
	return city_node["zones"].setdefault(
		zone_key,
		{"zone": zone_key, "zone_name": zone_key, "source_warehouse": None, "warehouses": [], "stores": [], "offices": []},
	)


def _warehouse_matches_view(warehouse, warehouse_view):
	# Warehouses that *represent a physical site* in the location view.
	# Sellable bin leaves are ALSO surfaced here even though their
	# ch_location_type is 'Store Bin', because in the SAP-aligned tree
	# (Path B Phase 2) the Sellable leaf IS the operational store warehouse
	# (its parent 'Store Group' is is_group=1 and gets filtered out elsewhere).
	# Hub Bins are hub sub-warehouses (Sellable-01, Sellable-02, quarantine,
	# etc.) — they belong to the location view alongside their Zone Warehouse.
	location_types = {"Store Warehouse", "Zone Warehouse", "Transit Warehouse", "Service Warehouse", "Hub Bin"}
	view = (warehouse_view or "all").strip().lower()
	w_type = (warehouse.ch_location_type or "").strip()
	is_sellable = (warehouse.ch_bin_type or "").strip() == "Sellable"

	if view == "location":
		return w_type in location_types or is_sellable
	if view == "operational":
		return (w_type not in location_types) and (not is_sellable)
	return True


def _serialize_tree(companies):
	result = []
	for company_node in companies.values():
		cities = []
		for city_node in company_node["cities"].values():
			city_node = dict(city_node)
			city_node["zones"] = list(city_node["zones"].values())
			cities.append(city_node)
		company_node = dict(company_node)
		company_node["cities"] = cities
		result.append(company_node)
	return result


@frappe.whitelist()
def get_location_access():
	user = getattr(frappe.session, "user", None)
	if not user or user == "Guest":
		return {"can_view": False, "can_manage": False}
	can_manage = has_role_setting(
		"location_manager_roles", _LOCATION_MANAGER_ROLES, user=user
	)
	return {
		"can_view": can_manage
		or has_role_setting("location_view_roles", _LOCATION_VIEW_ROLES, user=user),
		"can_manage": can_manage,
	}


@frappe.whitelist()
def list_companies():
	_check_view_permission("Company")
	scope = _get_location_scope()
	filters = {}
	companies = _scope_companies(scope)
	if companies is not None:
		filters["name"] = ("in", sorted(companies))
	return frappe.get_all(
		"Company",
		filters=filters,
		fields=["name", "company_name"],
		order_by="company_name",
		limit_page_length=_tree_limit(),
	)


@frappe.whitelist()
def list_warehouses(company=None, unassigned_only=0):
	_check_master_permission(("Warehouse", "read"), ("Company", "read"))
	scope = _get_location_scope(company)
	filters = {"disabled": 0, "is_group": 0}
	companies = _scope_companies(scope)
	if companies is not None:
		filters["company"] = (
			next(iter(companies)) if len(companies) == 1 else ("in", sorted(companies))
		)
	if int(unassigned_only or 0):
		filters["ch_zone"] = ["in", [None, ""]]
	rows = frappe.get_all(
		"Warehouse",
		filters=filters,
		fields=["name", "warehouse_name", "company", "ch_city", "ch_zone", "ch_store", "ch_location_type"],
		order_by="warehouse_name",
		limit_page_length=_tree_limit(),
	)
	return [row for row in rows if _warehouse_in_scope(scope, row)]


@frappe.whitelist()
def list_branches(company=None, unassigned_only=0):
	_check_master_permission(("Branch", "read"), ("Company", "read"))
	scope = _get_location_scope(company)
	filters = {}
	companies = _scope_companies(scope)
	if companies is not None:
		filters["ch_company"] = (
			next(iter(companies)) if len(companies) == 1 else ("in", sorted(companies))
		)
	if int(unassigned_only or 0):
		filters["ch_zone"] = ["in", [None, ""]]
	rows = frappe.get_all(
		"Branch",
		filters=filters,
		fields=["name", "branch", "ch_company", "ch_city", "ch_zone"],
		order_by="branch",
		limit_page_length=_tree_limit(),
	)
	return [row for row in rows if _branch_in_scope(scope, row)]


# Reserved bucket label used by _zone_bucket() for rows whose city / zone is
# NULL. It is NOT a real CH City / CH Store Zone record, so create / delete /
# rename operations targeting this literal name must be rejected — otherwise
# the UI shows a misleading "Deleted" toast and the bucket reappears on the
# next render (TC_003).
_SYNTHETIC_BUCKET = "Unassigned"


def _reject_synthetic(name, kind):
	if (name or "").strip() == _SYNTHETIC_BUCKET:
		frappe.throw(
			f"'{_SYNTHETIC_BUCKET}' is a virtual {kind} bucket for rows with no "
			f"{kind} assigned. Assign a real {kind} on the underlying Warehouse / "
			f"Branch / Store to clear it — it cannot be created, renamed or deleted."
		)


@frappe.whitelist(methods=["POST"])
def save_city(city_name, state=None, name=None, disabled=0, description=None, company=None):
	"""Create / update a CH City master row.

	``company`` is accepted but NOT persisted — City is a pure geographic
	master (Mumbai is Mumbai across all companies); company association lives
	on CH Store Zone where it belongs. The legacy ``company`` column on
	CH City is kept hidden for backward compatibility but is no longer written.
	``state`` is a Link to CH State; pass the value through unchanged so the
	link key is preserved verbatim (no .title() coercion).
	"""
	_check_master_permission(("CH City", "write" if name else "create"))
	if company:
		_assert_company_scope(company)
	_reject_synthetic(city_name, "city")
	_reject_synthetic(name, "city")
	clean_name = (city_name or "").strip().title()
	clean_state = (state or "").strip() or None
	if not clean_name:
		frappe.throw(_("City Name is required."), frappe.ValidationError)
	if clean_state:
		_require_named_permission("CH State", "read")
		if not frappe.db.exists("CH State", clean_state):
			frappe.throw(_("State {0} was not found.").format(clean_state), frappe.DoesNotExistError)

	# City masters are deduplicated by name (autoname = format:{city_name}, so
	# the record name IS the city name — there is no uniqueness suffix). A
	# "create" request for a city that already exists must therefore UPSERT,
	# not blindly insert: otherwise the PRIMARY-key collision surfaces to the
	# user as a raw DuplicateEntryError (e.g. two people both adding
	# "Chennai-33"). When no explicit `name` was supplied, fall back to any
	# existing row whose name already matches the cleaned city name.
	target = name or (clean_name if frappe.db.exists("CH City", clean_name) else None)

	if target:
		frappe.db.get_value("CH City", target, "name", for_update=True)
		doc = frappe.get_doc("CH City", target)
		_require_named_permission("CH City", "write", doc)
		doc.city_name = clean_name
		doc.state = clean_state
		doc.disabled = int(disabled or 0)
		doc.description = description
		doc.save()
	else:
		doc = frappe.new_doc("CH City")
		doc.city_name = clean_name
		doc.state = clean_state
		doc.disabled = int(disabled or 0)
		doc.description = description
		doc.insert()
	return doc.name


@frappe.whitelist(methods=["POST"])
def delete_city(name):
	_check_master_permission(("CH City", "delete"))
	_reject_synthetic(name, "city")
	frappe.db.get_value("CH City", name, "name", for_update=True)
	doc = frappe.get_doc("CH City", name)
	_require_named_permission("CH City", "delete", doc)
	zones = frappe.db.count("CH Store Zone", {"city": name})
	stores = frappe.db.count("CH Store", {"city": name})
	if zones or stores:
		frappe.throw(f"Cannot delete city '{name}' — {zones} zone(s) and {stores} store(s) are linked.")
	doc.delete()
	return True


# ---------------------------------------------------------------------------
# CH State master CRUD (admin-only — surfaced from the Location Hierarchy page)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_states():
	"""Return CH States ordered by state_name for picker dialogs."""
	_check_view_permission("CH State")
	return frappe.get_all(
		"CH State",
		filters={"disabled": 0},
		fields=["name", "state_name", "state_code", "country"],
		order_by="state_name",
		limit_page_length=_tree_limit(),
	)


@frappe.whitelist(methods=["POST"])
def save_state(state_name, state_code, country="India", name=None, disabled=0, description=None):
	"""Create / update a CH State row.

	``state_code`` is the GST / ISO 3166-2 code (e.g. KA, MH, 29). It is
	required and must be unique — enforced by the DocType field constraints.
	"""
	_check_master_permission(("CH State", "write" if name else "create"), ("Country", "read"))
	clean_name = (state_name or "").strip().title()
	clean_code = (state_code or "").strip().upper()
	if not clean_name:
		frappe.throw("State Name is required.")
	if not clean_code:
		frappe.throw("State Code is required (e.g. KA, MH, 29).")
	if not frappe.db.exists("Country", country or "India"):
		frappe.throw(_("Country {0} was not found.").format(country), frappe.DoesNotExistError)
	if name:
		frappe.db.get_value("CH State", name, "name", for_update=True)
		doc = frappe.get_doc("CH State", name)
		_require_named_permission("CH State", "write", doc)
		doc.state_name = clean_name
		doc.state_code = clean_code
		doc.country = country or "India"
		doc.disabled = int(disabled or 0)
		doc.description = description
		doc.save()
	else:
		doc = frappe.new_doc("CH State")
		doc.state_name = clean_name
		doc.state_code = clean_code
		doc.country = country or "India"
		doc.disabled = int(disabled or 0)
		doc.description = description
		doc.insert()
	return doc.name


@frappe.whitelist(methods=["POST"])
def delete_state(name):
	_check_master_permission(("CH State", "delete"))
	frappe.db.get_value("CH State", name, "name", for_update=True)
	doc = frappe.get_doc("CH State", name)
	_require_named_permission("CH State", "delete", doc)
	cities = frappe.db.count("CH City", {"state": name})
	if cities:
		frappe.throw(f"Cannot delete state '{name}' — {cities} city(ies) are linked.")
	doc.delete()
	return True


@frappe.whitelist(methods=["POST"])
def save_zone(company, city, zone_name, source_warehouse=None, name=None, description=None):
	_check_master_permission(
		("CH Store Zone", "write" if name else "create"),
		("Company", "read"),
		("CH City", "read"),
		("Warehouse", "write"),
	)
	scope = _assert_company_scope(company)
	_reject_synthetic(zone_name, "zone")
	_reject_synthetic(name, "zone")
	_reject_synthetic(city, "city")
	zone_name = _clean(zone_name)
	if not zone_name:
		frappe.throw(_("Zone Name is required."), frappe.ValidationError)
	if not frappe.db.exists("CH City", city):
		frappe.throw(_("City {0} was not found.").format(city), frappe.DoesNotExistError)
	if not _city_in_scope(scope, company, city, for_write=True):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	if source_warehouse:
		frappe.db.get_value("Warehouse", source_warehouse, "name", for_update=True)
		_assert_warehouse_scope(source_warehouse, scope=scope, permission_type="write")
		_validate_hub_candidate(source_warehouse, company=company, zone=name, city=city)
	old_source = None
	if name:
		frappe.db.get_value("CH Store Zone", name, "name", for_update=True)
		doc = frappe.get_doc("CH Store Zone", name)
		old_source = doc.source_warehouse
		_require_named_permission("CH Store Zone", "write", doc)
		if not _zone_in_scope(scope, doc.company, doc.city, doc.name, for_write=True):
			frappe.throw(_("The selected zone is outside your assigned scope."), frappe.PermissionError)
		doc.zone_name = zone_name
		doc.company = company
		doc.city = city
		doc.source_warehouse = source_warehouse or None
		if hasattr(doc, "description"):
			doc.description = description
		doc.save()
	else:
		doc = frappe.new_doc("CH Store Zone")
		doc.zone_name = zone_name
		doc.company = company
		doc.city = city
		doc.source_warehouse = source_warehouse or None
		if hasattr(doc, "description"):
			doc.description = description
		doc.insert()

	# Sync source warehouse tagging. CH Store Zone.source_warehouse stays
	# authoritative; shared city hubs keep Warehouse.ch_zone blank.
	if doc.source_warehouse and frappe.db.exists("Warehouse", doc.source_warehouse):
		sync_zone_source_warehouse_metadata(doc)
	if old_source and old_source != doc.source_warehouse and not _zones_using_hub(old_source):
		frappe.db.set_value(
			"Warehouse",
			old_source,
			{"ch_zone": None, "ch_location_type": None},
			update_modified=False,
		)
	return doc.name


@frappe.whitelist(methods=["POST"])
def delete_zone(name):
	_check_master_permission(("CH Store Zone", "delete"))
	_reject_synthetic(name, "zone")
	frappe.db.get_value("CH Store Zone", name, "name", for_update=True)
	doc, _scope = _assert_zone_scope(name, permission_type="delete")
	stores = frappe.db.count("CH Store", {"zone": name})
	whs = frappe.db.count("Warehouse", {"ch_zone": name})
	branches = frappe.db.count("Branch", {"ch_zone": name})
	if stores or whs or branches:
		frappe.throw(
			f"Cannot delete zone '{name}' — {stores} store(s), {whs} warehouse(s), {branches} office(s) are linked."
		)
	doc.delete()
	return True


@frappe.whitelist(methods=["POST"])
def assign_warehouse(warehouse, company=None, city=None, zone=None, location_type=None):
	_check_master_permission(
		("Warehouse", "write"), ("CH Store Zone", "write"), ("CH City", "read")
	)
	frappe.db.get_value("Warehouse", warehouse, "name", for_update=True)
	warehouse_doc = frappe.get_doc("Warehouse", warehouse)
	company = _clean(company) or warehouse_doc.company
	scope = _assert_company_scope(company)
	if not _warehouse_in_scope(scope, warehouse_doc):
		frappe.throw(_("The selected warehouse is outside your assigned scope."), frappe.PermissionError)
	if warehouse_doc.company != company:
		frappe.throw(_("Warehouse and company do not match."), frappe.ValidationError)
	location_type = _clean(location_type) or None
	if location_type and location_type not in LEAF_LOCATION_TYPES:
		frappe.throw(_("Invalid warehouse location type."), frappe.ValidationError)
	if city:
		if not frappe.db.exists("CH City", city):
			frappe.throw(_("City {0} was not found.").format(city), frappe.DoesNotExistError)
		if not _city_in_scope(scope, company, city, for_write=True):
			frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	zone_doc = None
	if zone:
		frappe.db.get_value("CH Store Zone", zone, "name", for_update=True)
		zone_doc, _scope = _assert_zone_scope(zone, scope=scope, permission_type="write")
		if zone_doc.company != company:
			frappe.throw(_("Zone and company do not match."), frappe.ValidationError)
		if city and zone_doc.city and zone_doc.city != city:
			frappe.throw(_("Zone and city do not match."), frappe.ValidationError)
		city = zone_doc.city or city
	if (location_type or "") == "Zone Warehouse":
		_validate_hub_candidate(warehouse, company=company, zone=zone, city=city)
		if zone_doc:
			old_hub = zone_doc.source_warehouse
			zone_doc.source_warehouse = warehouse
			zone_doc.save()
			sync_zone_source_warehouse_metadata(zone)
			if old_hub and old_hub != warehouse and not _zones_using_hub(old_hub):
				old_doc = frappe.get_doc("Warehouse", old_hub)
				old_doc.ch_zone = None
				old_doc.ch_location_type = None
				old_doc.save()
			return True
	warehouse_doc.ch_city = city or None
	warehouse_doc.ch_zone = zone or None
	warehouse_doc.ch_location_type = location_type
	warehouse_doc.save()
	# Cascade zone/city to child bins so they don't appear as Unassigned
	frappe.db.set_value(
		"Warehouse",
		{"parent_warehouse": warehouse, "is_group": 0},
		{"ch_city": city or None, "ch_zone": zone or None},
		update_modified=False,
	)
	return True


@frappe.whitelist(methods=["POST"])
def unassign_warehouse(warehouse):
	_check_master_permission(("Warehouse", "write"))
	frappe.db.get_value("Warehouse", warehouse, "name", for_update=True)
	doc, _scope = _assert_warehouse_scope(warehouse, permission_type="write")
	if doc.is_group:
		frappe.throw(_("Group warehouses cannot be unassigned from this page."), frappe.ValidationError)
	if frappe.db.exists("CH Store Zone", {"source_warehouse": warehouse}):
		frappe.throw(_("Remove this warehouse from its zones before unassigning it."), frappe.ValidationError)
	if frappe.db.exists("CH Store", {"warehouse": warehouse, "disabled": 0}):
		frappe.throw(_("An active store uses this warehouse."), frappe.ValidationError)
	doc.ch_city = None
	doc.ch_zone = None
	doc.ch_location_type = None
	doc.save()
	frappe.db.set_value(
		"Warehouse",
		{"parent_warehouse": warehouse, "is_group": 0},
		{"ch_city": None, "ch_zone": None},
		update_modified=False,
	)
	return True


@frappe.whitelist(methods=["POST"])
def unassign_city_hub(warehouse, company, city):
	_check_master_permission(("Warehouse", "write"), ("CH Store Zone", "write"))
	scope = _assert_company_scope(company)
	frappe.db.get_value("Warehouse", warehouse, "name", for_update=True)
	warehouse_doc, _scope = _assert_warehouse_scope(
		warehouse, scope=scope, permission_type="write"
	)
	if warehouse_doc.company != company or _clean(warehouse_doc.ch_city) != _clean(city):
		frappe.throw(_("Hub, company, and city do not match."), frappe.ValidationError)
	zones = frappe.get_all(
		"CH Store Zone",
		filters={"company": company, "city": city, "source_warehouse": warehouse},
		fields=["name"],
		limit_page_length=_tree_limit(),
	)
	for row in zones:
		frappe.db.get_value("CH Store Zone", row.name, "name", for_update=True)
		zone_doc, _scope = _assert_zone_scope(row.name, scope=scope, permission_type="write")
		zone_doc.source_warehouse = None
		zone_doc.save()
	if not _zones_using_hub(warehouse):
		warehouse_doc.ch_zone = None
		warehouse_doc.ch_location_type = None
		warehouse_doc.save()
	return {"warehouse": warehouse, "zones": [row.name for row in zones]}


@frappe.whitelist(methods=["POST"])
def create_hub(company, city, hub_name=None, warehouse=None, zones=None):
	"""Create or assign a city-level Distribution Hub (DC) and point the city's
	zones at it as their ``source_warehouse``.

	A physical hub commonly supplies every zone in a city, so by default this
	assigns the hub to ALL of the city's zones (new zones inherit it later).
	A zone-level override is still possible via Edit Zone → Source Warehouse.

	Parameters
	----------
	warehouse : str, optional
		Assign this existing leaf warehouse as the hub.
	hub_name : str, optional
		Create a new leaf Warehouse (``Zone Warehouse``) with this name and use
		it as the hub. Ignored when ``warehouse`` is supplied.
	zones : list | JSON | csv, optional
		Restrict to these zones; defaults to every zone in the city.
	"""
	_check_master_permission(
		("Warehouse", "create"),
		("Warehouse", "write"),
		("CH Store Zone", "write"),
		("Company", "read"),
		("CH City", "read"),
	)
	if not company or not city:
		frappe.throw("Company and City are required.")
	scope = _assert_company_scope(company)
	if not frappe.db.exists("CH City", city):
		frappe.throw(_("City {0} was not found.").format(city), frappe.DoesNotExistError)
	if not _city_in_scope(scope, company, city, for_write=True):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)

	if isinstance(zones, str) and zones.strip():
		try:
			zones = json.loads(zones)
		except Exception:
			zones = [z.strip() for z in zones.split(",") if z.strip()]
	if not zones:
		zones = frappe.get_all(
			"CH Store Zone",
			filters={"company": company, "city": city},
			pluck="name",
			limit_page_length=_tree_limit(),
		)
	if not isinstance(zones, (list, tuple)):
		frappe.throw(_("Zones must be a list."), frappe.ValidationError)
	zones = list(dict.fromkeys(_clean(value) for value in zones if _clean(value)))
	if len(zones) > _tree_limit():
		frappe.throw(_("Too many zones were requested."), frappe.ValidationError)
	if not zones:
		frappe.throw(f"Create a zone in {city} first, then add a hub.")
	zone_docs = []
	for zone in zones:
		frappe.db.get_value("CH Store Zone", zone, "name", for_update=True)
		zone_doc, _scope = _assert_zone_scope(zone, scope=scope, permission_type="write")
		if zone_doc.company != company or zone_doc.city != city:
			frappe.throw(_("Every zone must belong to the selected company and city."), frappe.ValidationError)
		zone_docs.append(zone_doc)

	warehouse = _clean(warehouse)
	if not warehouse:
		hub_name = _clean(hub_name)
		if not hub_name:
			frappe.throw("Provide an existing warehouse or a new hub name.")
		# Parent the DC under the city group so it inherits the right city (the
		# company root carries the HQ city and would fail the city check).
		parent = None
		sibling_hub = frappe.db.get_value(
			"CH Store Zone",
			{"company": company, "city": city, "source_warehouse": ["is", "set"]},
			"source_warehouse",
		)
		if sibling_hub:
			parent = frappe.db.get_value("Warehouse", sibling_hub, "parent_warehouse")
		if not parent:
			parent = frappe.db.get_value(
				"Warehouse",
				{"company": company, "is_group": 1, "ch_city": city, "ch_location_type": "City Group"},
				"name",
			)
		if not parent:
			parent = frappe.db.get_value(
				"Warehouse", {"company": company, "is_group": 1, "ch_city": city}, "name"
			)
		wh = frappe.new_doc("Warehouse")
		wh.warehouse_name = hub_name
		wh.company = company
		wh.is_group = 0
		wh.ch_city = city
		wh.ch_zone = zones[0] if len(zones) == 1 else None
		wh.ch_location_type = "Zone Warehouse"
		if parent:
			wh.parent_warehouse = parent
		wh.insert()
		warehouse = wh.name
	elif not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(f"Warehouse {warehouse} not found.")
	else:
		frappe.db.get_value("Warehouse", warehouse, "name", for_update=True)
		_assert_warehouse_scope(warehouse, scope=scope, permission_type="write")

	# Validate against every target zone (all in the same city → allowed).
	for z in zones:
		_validate_hub_candidate(warehouse, company=company, zone=z, city=city)
	for zone_doc in zone_docs:
		zone_doc.source_warehouse = warehouse
		zone_doc.save()
		sync_zone_source_warehouse_metadata(zone_doc)
	return {"warehouse": warehouse, "zones": zones}


@frappe.whitelist(methods=["POST"])
def add_hub_bin_at_city(company, city, label, hub=None):
	"""Create a Hub Bin under the city's hub (any zone the hub serves works —
	bins render under the hub regardless of the creating zone)."""
	_check_master_permission(("Warehouse", "create"), ("CH Store Zone", "read"))
	scope = _assert_company_scope(company)
	if not _city_in_scope(scope, company, city, for_write=True):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	zones = frappe.get_all(
		"CH Store Zone",
		filters={"company": company, "city": city, "source_warehouse": ["is", "set"]},
		fields=["name", "source_warehouse"],
		limit_page_length=_tree_limit(),
	)
	zones = [
		z for z in zones
		if _zone_in_scope(scope, company, city, z.name, for_write=True)
	]
	if hub:
		_assert_warehouse_scope(hub, scope=scope, permission_type="read")
		zones = [z for z in zones if z.source_warehouse == hub]
	if not zones:
		frappe.throw("No hub is assigned for this city yet. Add a hub first.")
	return create_hub_bin(zones[0].name, label)


@frappe.whitelist(methods=["POST"])
def assign_office(branch, company=None, city=None, zone=None):
	_check_master_permission(("Branch", "write"), ("CH Store Zone", "read"), ("CH City", "read"))
	if not company:
		frappe.throw(_("Company is required."), frappe.ValidationError)
	scope = _assert_company_scope(company)
	frappe.db.get_value("Branch", branch, "name", for_update=True)
	doc = frappe.get_doc("Branch", branch)
	_require_named_permission("Branch", "write", doc)
	if doc.ch_company and not _branch_in_scope(scope, doc):
		frappe.throw(_("The selected office is outside your assigned scope."), frappe.PermissionError)
	if city and not _city_in_scope(scope, company, city, for_write=True):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	if zone:
		zone_doc, _scope = _assert_zone_scope(zone, scope=scope, permission_type="write")
		if zone_doc.company != company or (city and zone_doc.city != city):
			frappe.throw(_("Office, zone, city, and company do not match."), frappe.ValidationError)
		city = zone_doc.city or city
	doc.ch_company = company
	doc.ch_city = city or None
	doc.ch_zone = zone or None
	doc.save()
	return True


@frappe.whitelist(methods=["POST"])
def unassign_office(branch):
	_check_master_permission(("Branch", "write"))
	frappe.db.get_value("Branch", branch, "name", for_update=True)
	doc = frappe.get_doc("Branch", branch)
	_require_named_permission("Branch", "write", doc)
	scope = _get_location_scope(doc.ch_company)
	if not _branch_in_scope(scope, doc):
		frappe.throw(_("The selected office is outside your assigned scope."), frappe.PermissionError)
	doc.ch_company = None
	doc.ch_city = None
	doc.ch_zone = None
	doc.save()
	return True


@frappe.whitelist(methods=["POST"])
def create_office(branch, company=None, city=None, zone=None):
	_check_master_permission(("Branch", "create"), ("CH Store Zone", "read"), ("CH City", "read"))
	if not company:
		frappe.throw(_("Company is required."), frappe.ValidationError)
	scope = _assert_company_scope(company)
	if city and not _city_in_scope(scope, company, city, for_write=True):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	if zone:
		zone_doc, _scope = _assert_zone_scope(zone, scope=scope, permission_type="write")
		if zone_doc.company != company or (city and zone_doc.city != city):
			frappe.throw(_("Office, zone, city, and company do not match."), frappe.ValidationError)
		city = zone_doc.city or city
	branch = _clean(branch)
	if not branch:
		frappe.throw(_("Office Name is required."), frappe.ValidationError)
	if frappe.db.exists("Branch", branch):
		frappe.throw(f"Office '{branch}' already exists")
	doc = frappe.new_doc("Branch")
	doc.branch = branch
	doc.ch_company = company or None
	doc.ch_city = city or None
	doc.ch_zone = zone or None
	doc.insert()
	return doc.name


@frappe.whitelist(methods=["POST"])
def save_store(company, city, zone, store_name, store_code=None, warehouse=None, branch=None, name=None,
			   store_status=None, opening_date=None):
	_check_master_permission(
		("CH Store", "write" if name else "create"),
		("Company", "read"),
		("CH City", "read"),
		("CH Store Zone", "read"),
		("Warehouse", "read"),
		("Warehouse", "create"),
		("Branch", "read"),
	)
	if not company or not city or not zone:
		frappe.throw(_("Company, City, and Zone are required."), frappe.ValidationError)
	store_name = _clean(store_name)
	if not store_name:
		frappe.throw(_("Store Name is required."), frappe.ValidationError)
	scope = _assert_company_scope(company)
	if not _city_in_scope(scope, company, city, for_write=True):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	zone_doc, _scope = _assert_zone_scope(zone, scope=scope, permission_type="write")
	if zone_doc.company != company or zone_doc.city != city:
		frappe.throw(_("Store, zone, city, and company do not match."), frappe.ValidationError)
	if name:
		frappe.db.get_value("CH Store", name, "name", for_update=True)
		doc, _scope = _assert_store_scope(name, permission_type="write")
	else:
		doc = frappe.new_doc("CH Store")
	doc.company = company
	doc.city = city
	doc.zone = zone
	if store_code:
		doc.store_code = store_code
	doc.store_name = store_name
	if warehouse:
		warehouse_doc, _scope = _assert_warehouse_scope(
			warehouse, scope=scope, permission_type="read"
		)
		if warehouse_doc.company != company:
			frappe.throw(_("Store and warehouse companies do not match."), frappe.ValidationError)
		doc.warehouse = warehouse
	if branch:
		branch_doc = frappe.get_doc("Branch", branch)
		_require_named_permission("Branch", "read", branch_doc)
		if branch_doc.get("ch_company") and branch_doc.get("ch_company") != company:
			frappe.throw(_("Store and office companies do not match."), frappe.ValidationError)
		doc.branch = branch
	if store_status:
		doc.store_status = store_status
	if opening_date:
		doc.opening_date = opening_date
	doc.save() if name else doc.insert()
	if not doc.warehouse:
		from ch_item_master.ch_core.warehouse_geo import provision_store_warehouse

		provision_store_warehouse(doc.name)
	return doc.name


@frappe.whitelist(methods=["POST"])
def delete_store(name):
	_check_master_permission(("CH Store", "delete"))
	frappe.db.get_value("CH Store", name, "name", for_update=True)
	doc, _scope = _assert_store_scope(name, permission_type="delete")
	doc.delete()
	return True


@frappe.whitelist(methods=["POST"])
def create_store_bin(store, bin_type, custom_suffix=None):
	"""Create one additional stock-state bin (Warehouse) attached to a CH Store.

	Used by the Location Hierarchy page's "+ Add Bin" action so retail-ops
	users can backfill a missing standard bin (e.g. add a Buyback bin to a
	store that was created before STORE_BIN_TYPES included it).

	Parameters
	----------
	store : str
		Name of the CH Store this bin belongs to.
	bin_type : str
		Must be one of the canonical STORE_BIN_TYPES labels (currently:
		In-Transit, Damaged, Disposed, Reserved, Buyback). "Sellable" is
		rejected because the store warehouse itself IS the Sellable bin.
	custom_suffix : str | None
		Ignored — kept for API stability. The warehouse-name suffix is
		always taken from the canonical STORE_BIN_TYPES mapping so naming
		stays consistent with bins created by ``ensure_store_bins``.

	Returns
	-------
	dict  { "warehouse": <name>, "bin_type": <label>, "created": bool }

	Notes
	-----
	The ``ch_bin_type`` custom field is a Select with a fixed option list.
	Adding non-standard bin types here would fail validation at insert time
	and silently break the warehouse — so we constrain to STORE_BIN_TYPES
	rather than expanding the Select.

	Idempotent: if a Warehouse already exists for (store, bin_type) it is
	returned with ``created=False``.
	"""
	_check_master_permission(("Warehouse", "create"), ("CH Store", "read"))

	from ch_item_master.ch_core.doctype.ch_store.ch_store import STORE_BIN_TYPES

	bin_type = (bin_type or "").strip()
	if not bin_type:
		frappe.throw("Bin Type is required.")
	if bin_type.lower() == "sellable":
		frappe.throw(
			"The Sellable bin is the store warehouse itself and cannot be "
			"created as a separate bin."
		)

	canonical = {label: suffix for label, suffix in STORE_BIN_TYPES}
	if bin_type not in canonical:
		allowed = ", ".join(canonical.keys())
		frappe.throw(
			f"Bin Type '{bin_type}' is not supported. "
			f"Allowed values: {allowed}."
		)
	suffix = canonical[bin_type]

	frappe.db.get_value("CH Store", store, "name", for_update=True)
	st, _scope = _assert_store_scope(store)
	if not st.warehouse:
		frappe.throw(
			f"Store {st.name} has no base warehouse assigned. "
			"Assign one before adding bins."
		)
	if not st.store_code:
		frappe.throw(f"Store {st.name} is missing a store_code.")

	# Idempotency: one bin per (store, bin_type).
	existing = frappe.db.exists(
		"Warehouse",
		{"company": st.company, "ch_store": st.name, "ch_bin_type": bin_type},
	)
	if existing:
		return {"warehouse": existing, "bin_type": bin_type, "created": False}

	# Bins are siblings of the base warehouse so each can post SLEs directly.
	parent_warehouse = frappe.db.get_value(
		"Warehouse", st.warehouse, "parent_warehouse"
	)

	wh = frappe.new_doc("Warehouse")
	wh.warehouse_name = f"{st.store_code}-{suffix}"
	if parent_warehouse:
		wh.parent_warehouse = parent_warehouse
	wh.company = st.company
	wh.is_group = 0
	wh.ch_city = st.city
	wh.ch_zone = st.zone
	wh.ch_store = st.name
	wh.ch_location_type = "Store Bin"
	wh.ch_bin_type = bin_type
	wh.insert()

	return {"warehouse": wh.name, "bin_type": bin_type, "created": True}


# ---------------------------------------------------------------------------
# Hub Bins (Phase 4 — hub-side sub-warehouses)
# ---------------------------------------------------------------------------
#
# Unlike store bins (fixed set: Damaged / Demo / Buyback), hub bins are
# free-form: an operator can create ``Sellable-01``, ``Sellable-02``,
# ``Quarantine``, ``Inbound-Dock-A`` etc. as siblings of the Zone
# Warehouse. This mirrors SAP Storage Bins under a Plant and Oracle
# Locators under an Inventory Org — the hub is the physical facility,
# hub bins are addressable sub-locations inside it.
#
# Hub bins are parented under the ZONE GROUP (same parent as the Zone
# Warehouse leaf) so we never have to flip the Zone Warehouse's
# ``is_group`` flag (which would break Stock Ledger Entries).
#
# The ``ch_hub_bin_type`` custom field carries the free-form label so
# reports can filter/aggregate without parsing warehouse names.


_HUB_BIN_LABEL_MAX = 40


def _resolve_hub_context(zone, scope=None):
	"""Return (zone_doc, hub_warehouse, hub_parent) for a zone.

	``hub_parent`` is the Warehouse we'll attach new Hub Bins under. We
	prefer the Zone Warehouse's ``parent_warehouse`` (the Zone Group);
	falling back to the Zone Warehouse itself only if no parent group
	exists (very old sites where the hub sits directly under the company
	root).
	"""
	zone_doc, scope = _assert_zone_scope(zone, scope=scope)
	hub = zone_doc.source_warehouse
	if not hub or not frappe.db.exists("Warehouse", hub):
		frappe.throw(
			frappe._("Zone {0} has no Hub warehouse assigned. Assign one first.").format(
				frappe.bold(zone_doc.zone_name or zone),
			),
			title=frappe._("Hub Not Assigned"),
		)
	_assert_warehouse_scope(hub, scope=scope)
	_validate_hub_candidate(
		hub, company=zone_doc.company, zone=zone_doc.name, city=zone_doc.city
	)
	hub_parent = frappe.db.get_value("Warehouse", hub, "parent_warehouse") or hub
	return zone_doc, hub, hub_parent


def _sanitize_hub_bin_label(label):
	import re
	label = (label or "").strip()
	if not label:
		frappe.throw(frappe._("Hub Bin label is required."))
	if len(label) > _HUB_BIN_LABEL_MAX:
		frappe.throw(frappe._("Hub Bin label must be {0} characters or fewer.").format(_HUB_BIN_LABEL_MAX))
	# Warehouse names must be safe for URLs and file paths. Allow letters,
	# digits, spaces, dashes and underscores; reject everything else.
	if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 _\-]*$", label):
		frappe.throw(
			frappe._("Hub Bin label may only contain letters, digits, spaces, dashes and underscores."),
			title=frappe._("Invalid Label"),
		)
	return label


@frappe.whitelist()
def list_hub_bins(zone):
	"""Return the Hub Bin warehouses attached to a zone (for the UI)."""
	_check_view_permission("CH Store Zone", "Warehouse")
	zone_doc, scope = _assert_zone_scope(zone)
	zone_doc, hub, _hub_parent = _resolve_hub_context(zone, scope=scope)
	rows = frappe.get_all(
		"Warehouse",
		filters={
			"disabled": 0,
			"ch_zone": zone_doc.name,
			"ch_location_type": "Hub Bin",
		},
		fields=[
			"name", "warehouse_name", "company", "ch_city", "ch_zone", "ch_store",
			"ch_hub_bin_type", "parent_warehouse",
		],
		order_by="ch_hub_bin_type asc",
		limit_page_length=_tree_limit(),
	)
	return [row for row in rows if _warehouse_in_scope(scope, row)]


@frappe.whitelist(methods=["POST"])
def create_hub_bin(zone, label):
	"""Create one Hub Bin (child warehouse) under the given zone's hub.

	Parameters
	----------
	zone : str
		Name of the CH Store Zone (must already have ``source_warehouse``).
	label : str
		Free-form identifier for the bin (e.g. ``Sellable-01``). Sanitized
		and used both for the warehouse-name suffix and for
		``ch_hub_bin_type``. Must be unique within the zone.

	Returns
	-------
	dict  { "warehouse": <name>, "label": <label>, "created": bool }

	Idempotent: if a Hub Bin with the same (zone, label) already exists,
	it is returned with ``created=False``.
	"""
	_check_master_permission(("Warehouse", "create"), ("CH Store Zone", "write"))
	label = _sanitize_hub_bin_label(label)
	zone_doc, scope = _assert_zone_scope(zone, permission_type="write")
	zone_doc, hub, hub_parent = _resolve_hub_context(zone, scope=scope)
	frappe.db.get_value("Warehouse", hub, "name", for_update=True)

	# Idempotency: one Hub Bin per (zone, label).
	existing = frappe.db.get_value(
		"Warehouse",
		{
			"ch_zone": zone_doc.name,
			"ch_location_type": "Hub Bin",
			"ch_hub_bin_type": label,
		},
		"name",
	)
	if existing:
		return {"warehouse": existing, "label": label, "created": False}

	# Compose the warehouse_name from the hub's short name so the tree
	# reads cleanly. Strip the " - <ABBR>" suffix that ERPNext appends so
	# we don't double-suffix on autoname.
	hub_display = frappe.db.get_value("Warehouse", hub, "warehouse_name") or hub
	warehouse_name = f"{hub_display}-{label}"

	wh = frappe.new_doc("Warehouse")
	wh.warehouse_name = warehouse_name
	wh.parent_warehouse = hub_parent
	wh.company = zone_doc.company
	wh.is_group = 0
	wh.ch_city = zone_doc.city
	wh.ch_zone = zone_doc.name
	wh.ch_location_type = "Hub Bin"
	wh.ch_hub_bin_type = label
	try:
		wh.insert()
	except frappe.DuplicateEntryError:
		# Race — another request beat us. Re-resolve and return.
		existing = frappe.db.get_value(
			"Warehouse",
			{
				"ch_zone": zone_doc.name,
				"ch_location_type": "Hub Bin",
				"ch_hub_bin_type": label,
			},
			"name",
		)
		return {"warehouse": existing, "label": label, "created": False}

	return {"warehouse": wh.name, "label": label, "created": True}


# ---------------------------------------------------------------------------
# Link-picker query helpers — proper bifurcation for Hub / Store / Other
# ---------------------------------------------------------------------------
#
# The "Assign Warehouse" dialog on the Location Hierarchy page is invoked
# from three distinct actions (Assign Hub, +Hub, Assign Other Warehouse).
# A single loose ``{is_group:0, company}`` filter used to leak every leaf
# warehouse in the company — including store-owned Sellable/Damaged/Demo/
# Buyback bins and the ERPNext-auto ``Goods In Transit`` — into the picker.
#
# These whitelisted, sanitized query functions are used as the ``query``
# hook on the Link field so bifurcation happens server-side and null
# semantics for ``ch_bin_type`` / ``ch_zone`` / ``warehouse_type`` are
# handled correctly (Frappe's client-side ``['in', ['', null]]`` filter
# does not survive the MariaDB null-in-list check).


def _wh_search_txt(txt):
	# Match Frappe's default LIKE pattern for Link picker searches.
	return f"%{_clean(txt)[:140]}%"


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def report_warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Store-centric Warehouse picker for report filters.

	Business users filter stock reports by STORE, not by the raw warehouse
	tree — the default picker leaks every group node, store bin and transit
	warehouse. This query lists only the two kinds of stock locations a
	report reader cares about, searchable by the names they actually know:

	  * each enabled store's SELLABLE warehouse (``CH Store.warehouse``) —
	    searchable by store name / store code, shown with the store name
	  * Distribution Hubs (``ch_location_type = 'Zone Warehouse'`` leaves)

	The picked value is still a Warehouse PK, so every report's SQL keeps
	working unchanged — the store→sellable-bin mapping happens here.
	Wired to report filters globally by ``report_warehouse_picker.js``.
	"""
	_check_warehouse_picker_permission("Warehouse", "CH Store", "Company")
	if doctype != "Warehouse":
		frappe.throw(_("Invalid link query DocType."), frappe.ValidationError)
	filters = filters or {}
	start, page_len = _bounded_page(start, page_len)
	values = {"txt": _wh_search_txt(txt), "start": start, "page_len": page_len}
	scope = _get_location_scope(filters.get("company"))
	scope_cond = _warehouse_scope_condition(scope, values, prefix="report_scope")
	return frappe.db.sql(
		f"""
		SELECT
			wh.name,
			COALESCE(st.store_name, wh.warehouse_name) AS display,
			CASE WHEN st.name IS NULL THEN 'Distribution Hub' ELSE 'Store' END AS kind
		FROM `tabWarehouse` wh
		LEFT JOIN `tabCH Store` st
			ON st.warehouse = wh.name AND st.disabled = 0
		WHERE wh.disabled = 0
		  AND wh.is_group = 0
		  AND (st.name IS NOT NULL OR wh.ch_location_type = 'Zone Warehouse')
		  AND {scope_cond}
		  AND (
				wh.name LIKE %(txt)s
			 OR wh.warehouse_name LIKE %(txt)s
			 OR st.store_name LIKE %(txt)s
			 OR st.name LIKE %(txt)s
		  )
		ORDER BY (st.name IS NULL), COALESCE(st.store_name, wh.warehouse_name)
		LIMIT %(start)s, %(page_len)s
		""",
		values,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def hub_warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Query for the Zone Hub (Zone Warehouse) picker.

	A warehouse is Hub-eligible when it is:
	  * a leaf (is_group=0) and not disabled
	  * scoped to the correct company
	  * NOT owned by any CH Store (ch_store IS NULL)
	  * NOT tagged as a Store Warehouse / Store Bin / Hub Bin
	  * NOT a Transit warehouse (warehouse_type='Transit')
	  * NOT carrying a store-bin type (ch_bin_type IS NULL / '')
	  * either untagged OR already tagged to this zone / same-city zones
	"""
	_check_master_permission(("Warehouse", "read"), ("CH Store Zone", "read"), ("Company", "read"))
	if doctype != "Warehouse":
		frappe.throw(_("Invalid link query DocType."), frappe.ValidationError)
	filters = filters or {}
	company = filters.get("company")
	zone = filters.get("zone")
	city = filters.get("city")
	scope = _get_location_scope(company)
	if city and company and not _city_in_scope(scope, company, city):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	if zone:
		zone_doc, _scope = _assert_zone_scope(zone, scope=scope)
		if company and zone_doc.company != company:
			frappe.throw(_("Zone and company do not match."), frappe.ValidationError)
		if city and zone_doc.city != city:
			frappe.throw(_("Zone and city do not match."), frappe.ValidationError)
	start, page_len = _bounded_page(start, page_len)
	searchfield = _safe_warehouse_searchfield(searchfield)
	values = {"txt": _wh_search_txt(txt), "start": start, "page_len": page_len}
	conditions = [
		"wh.disabled = 0",
		"wh.is_group = 0",
		"(wh.ch_store IS NULL OR wh.ch_store = '')",
		"(wh.warehouse_type IS NULL OR wh.warehouse_type != 'Transit')",
		"(wh.ch_location_type IS NULL OR wh.ch_location_type = ''"
		" OR wh.ch_location_type = 'Zone Warehouse')",
		"(wh.ch_bin_type IS NULL OR wh.ch_bin_type = '')",
		f"wh.`{searchfield}` LIKE %(txt)s",
	]
	conditions.append(_warehouse_scope_condition(scope, values, prefix="hub_scope"))
	if company:
		conditions.append("wh.company = %(company)s")
		values["company"] = company
	if city:
		conditions.append("(wh.ch_city IS NULL OR wh.ch_city = '' OR wh.ch_city = %(city)s)")
		conditions.append(
			"""
			NOT EXISTS (
				SELECT 1
				FROM `tabCH Store Zone` source_zone
				WHERE source_zone.source_warehouse = wh.name
				  AND IFNULL(source_zone.city, '') != ''
				  AND source_zone.city != %(city)s
			)
			"""
		)
		values["city"] = city
	if zone:
		if city:
			conditions.append(
				"""
				(
					wh.ch_zone IS NULL OR wh.ch_zone = '' OR wh.ch_zone = %(zone)s
					OR EXISTS (
						SELECT 1
						FROM `tabCH Store Zone` tagged_zone
						WHERE tagged_zone.name = wh.ch_zone
						  AND tagged_zone.city = %(city)s
					)
				)
				"""
			)
		else:
			conditions.append(
				"(wh.ch_zone IS NULL OR wh.ch_zone = '' OR wh.ch_zone = %(zone)s)"
			)
		values["zone"] = zone
	else:
		if city:
			conditions.append(
				"""
				(
					wh.ch_zone IS NULL OR wh.ch_zone = ''
					OR EXISTS (
						SELECT 1
						FROM `tabCH Store Zone` tagged_zone
						WHERE tagged_zone.name = wh.ch_zone
						  AND tagged_zone.city = %(city)s
					)
				)
				"""
			)
		else:
			conditions.append("(wh.ch_zone IS NULL OR wh.ch_zone = '')")

	where_clause = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT wh.name, IFNULL(wh.warehouse_name, wh.name),
		       IFNULL(wh.ch_location_type, '')
		FROM `tabWarehouse` wh
		WHERE {where_clause}
		ORDER BY
			CASE WHEN wh.ch_location_type = 'Zone Warehouse' THEN 0 ELSE 1 END,
			wh.name
		LIMIT %(start)s, %(page_len)s
		""",
		values,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def other_warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Query for the "Other Warehouses" picker (Transit / Service / Other).

	Excludes anything that is already a Store or Hub asset:
	  * not owned by a CH Store
	  * not tagged Store Warehouse / Store Bin / Zone Warehouse / Hub Bin
	  * not carrying a store-bin type
	"""
	_check_master_permission(("Warehouse", "read"), ("Company", "read"))
	if doctype != "Warehouse":
		frappe.throw(_("Invalid link query DocType."), frappe.ValidationError)
	filters = filters or {}
	company = filters.get("company")
	scope = _get_location_scope(company)
	start, page_len = _bounded_page(start, page_len)
	searchfield = _safe_warehouse_searchfield(searchfield)
	values = {"txt": _wh_search_txt(txt), "start": start, "page_len": page_len}
	conditions = [
		"wh.disabled = 0",
		"wh.is_group = 0",
		"(wh.ch_store IS NULL OR wh.ch_store = '')",
		"(wh.ch_location_type IS NULL OR wh.ch_location_type = ''"
		" OR wh.ch_location_type IN ('Transit Warehouse','Service Warehouse','Other'))",
		"(wh.ch_bin_type IS NULL OR wh.ch_bin_type = '')",
		f"wh.`{searchfield}` LIKE %(txt)s",
	]
	conditions.append(_warehouse_scope_condition(scope, values, prefix="other_scope"))
	if company:
		conditions.append("wh.company = %(company)s")
		values["company"] = company

	where_clause = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT wh.name, IFNULL(wh.warehouse_name, wh.name),
		       IFNULL(wh.ch_location_type, '')
		FROM `tabWarehouse` wh
		WHERE {where_clause}
		ORDER BY wh.name
		LIMIT %(start)s, %(page_len)s
		""",
		values,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def sellable_warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Query for the Sellable Warehouse picker used by the Add Store dialog.

	Mirrors the client-side ``sellableWarehouseFilters`` but adds the
	``warehouse_type != 'Transit'`` guard that the client filter cannot
	express (null-in-list semantics). A warehouse is Sellable-eligible when:
	  * leaf, enabled, correct company
	  * not owned by any CH Store
	  * not already selected as any active CH Store.warehouse
	  * ch_bin_type is blank or 'Sellable'
	  * ch_location_type is blank or Store Warehouse / Store Bin
	  * warehouse_type is not 'Transit'
	  * either untagged OR already scoped to this zone
	"""
	_check_master_permission(("Warehouse", "read"), ("CH Store", "read"), ("CH Store Zone", "read"))
	if doctype != "Warehouse":
		frappe.throw(_("Invalid link query DocType."), frappe.ValidationError)
	filters = filters or {}
	company = filters.get("company")
	zone = filters.get("zone")
	city = filters.get("city")
	scope = _get_location_scope(company)
	if city and company and not _city_in_scope(scope, company, city):
		frappe.throw(_("The selected city is outside your assigned scope."), frappe.PermissionError)
	if zone:
		zone_doc, _scope = _assert_zone_scope(zone, scope=scope)
		if company and zone_doc.company != company:
			frappe.throw(_("Zone and company do not match."), frappe.ValidationError)
		if city and zone_doc.city != city:
			frappe.throw(_("Zone and city do not match."), frappe.ValidationError)
	start, page_len = _bounded_page(start, page_len)
	searchfield = _safe_warehouse_searchfield(searchfield)
	values = {"txt": _wh_search_txt(txt), "start": start, "page_len": page_len}
	conditions = [
		"wh.disabled = 0",
		"wh.is_group = 0",
		"(wh.ch_store IS NULL OR wh.ch_store = '')",
		"""
		NOT EXISTS (
			SELECT 1
			FROM `tabCH Store` assigned_store
			WHERE assigned_store.warehouse = wh.name
			  AND IFNULL(assigned_store.disabled, 0) = 0
		)
		""",
		"(wh.warehouse_type IS NULL OR wh.warehouse_type != 'Transit')",
		"(wh.ch_bin_type IS NULL OR wh.ch_bin_type IN ('', 'Sellable'))",
		"(wh.ch_location_type IS NULL OR wh.ch_location_type IN ('', 'Store Warehouse', 'Store Bin'))",
		f"wh.`{searchfield}` LIKE %(txt)s",
	]
	conditions.append(_warehouse_scope_condition(scope, values, prefix="sellable_scope"))
	if company:
		conditions.append("wh.company = %(company)s")
		values["company"] = company
	if city:
		conditions.append("(wh.ch_city IS NULL OR wh.ch_city = '' OR wh.ch_city = %(city)s)")
		values["city"] = city
	if zone:
		conditions.append(
			"(wh.ch_zone IS NULL OR wh.ch_zone = '' OR wh.ch_zone = %(zone)s)"
		)
		values["zone"] = zone
	else:
		conditions.append("(wh.ch_zone IS NULL OR wh.ch_zone = '')")

	where_clause = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT wh.name, IFNULL(wh.warehouse_name, wh.name),
		       IFNULL(wh.ch_bin_type, '')
		FROM `tabWarehouse` wh
		WHERE {where_clause}
		ORDER BY wh.name
		LIMIT %(start)s, %(page_len)s
		""",
		values,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def master_city_query(doctype, txt, searchfield, start, page_len, filters):
	"""Scoped CH City picker for Location Hierarchy manager dialogs."""
	_check_master_permission(("CH City", "read"), ("CH State", "read"), ("Country", "read"))
	if doctype != "CH City":
		frappe.throw(_("Invalid link query DocType."), frappe.ValidationError)
	filters = filters or {}
	start, page_len = _bounded_page(start, page_len)
	clean_txt = _clean(txt)[:140]
	txt_like = f"%{clean_txt}%"
	prefix_like = f"{clean_txt}%"
	values = {
		"txt": txt_like,
		"prefix": prefix_like,
		"start": start,
		"page_len": page_len,
	}
	conditions = ["IFNULL(c.disabled, 0) = 0"]
	company = filters.get("company")
	scope = _get_location_scope(company)
	if not scope.get("bypass") and (
		not company or company not in set(scope.get("direct_companies") or ())
	):
		cities = tuple(sorted(set(scope.get("cities") or ())))
		if not cities:
			conditions.append("1=0")
		else:
			conditions.append("c.name IN %(allowed_cities)s")
			values["allowed_cities"] = cities
	if filters.get("state"):
		conditions.append("c.state = %(state)s")
		values["state"] = filters["state"]
	if filters.get("country"):
		conditions.append("c.country = %(country)s")
		values["country"] = filters["country"]
	conditions.append(
		"(c.name LIKE %(txt)s OR c.city_name LIKE %(txt)s"
		" OR c.state LIKE %(txt)s OR c.country LIKE %(txt)s)"
	)
	where_clause = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT c.name,
		       IFNULL(c.city_name, c.name),
		       CONCAT_WS(', ', c.state, c.country)
		FROM `tabCH City` c
		WHERE {where_clause}
		ORDER BY
			CASE WHEN c.name LIKE %(prefix)s THEN 0
			     WHEN c.city_name LIKE %(prefix)s THEN 1
			     ELSE 2 END,
			c.name
		LIMIT %(start)s, %(page_len)s
		""",
		values,
	)
