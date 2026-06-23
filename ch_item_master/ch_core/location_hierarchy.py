import frappe


def ensure_city(company, city_name, state=None):
	if not city_name:
		return None

	clean_city = city_name.strip().title()
	if not clean_city:
		return None

	existing = frappe.db.get_value("CH City", {"state": state, "city_name": clean_city}, "name") if state else None
	if not existing:
		existing = frappe.db.get_value("CH City", {"city_name": clean_city}, "name")
	if existing:
		return existing

	city = frappe.new_doc("CH City")
	city.city_name = clean_city
	city.state = state or None
	city.insert(ignore_permissions=True)
	return city.name


def backfill_location_hierarchy():
	"""Backfill Company → City → Zone links from existing CH Store data."""
	if not frappe.db.table_exists("CH City"):
		return

	stores = frappe.get_all(
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
				warehouse_updates["ch_location_type"] = "Store Warehouse"
			frappe.db.set_value("Warehouse", store.warehouse, warehouse_updates, update_modified=False)

		if store.branch and frappe.db.exists("Branch", store.branch):
			frappe.db.set_value(
				"Branch",
				store.branch,
				{"ch_company": store.company, "ch_city": city, "ch_zone": store.zone or None},
				update_modified=False,
			)

	# Mark zone source warehouses as zone warehouses.
	for zone in frappe.get_all("CH Store Zone", fields=["name", "city", "source_warehouse"]):
		if zone.source_warehouse and frappe.db.exists("Warehouse", zone.source_warehouse):
			frappe.db.set_value(
				"Warehouse",
				zone.source_warehouse,
				{"ch_city": zone.city, "ch_zone": zone.name, "ch_location_type": "Zone Warehouse"},
				update_modified=False,
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

	stores = frappe.get_all(
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

	A "Hub" is a Warehouse Group sitting at the city level that aggregates
	stock for all stores in the zone. We don't reparent existing Store
	Warehouses (that would rewrite the warehouse tree and is risky for
	in-flight stock); we only:

	  1. Create the Hub warehouse if missing (idempotent — keyed by name).
	  2. Stamp ch_location_type='Zone Warehouse' + ch_city + ch_zone on it.
	  3. Point the Zone's source_warehouse at it.

	Result: every zone has a designated central warehouse retail users can
	think of as the "back-end" / consolidation point, without disturbing
	stock ledgers on the leaf store warehouses.

	Idempotent. Safe to run repeatedly from after_migrate.
	"""
	if not frappe.db.table_exists("CH Store Zone"):
		return
	if not frappe.db.exists("Custom Field", {"dt": "Warehouse", "fieldname": "ch_location_type"}):
		return

	zones = frappe.get_all(
		"CH Store Zone",
		fields=["name", "zone_name", "company", "city", "source_warehouse"],
	)
	for zone in zones:
		if not zone.company:
			continue

		hub = zone.source_warehouse
		# 1. If a source_warehouse is already set and exists, just stamp it.
		if hub and frappe.db.exists("Warehouse", hub):
			frappe.db.set_value(
				"Warehouse", hub,
				{
					"ch_city": zone.city,
					"ch_zone": zone.name,
					"ch_location_type": "Zone Warehouse",
				},
				update_modified=False,
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
				wh.is_group = 1
				wh.ch_city = zone.city
				wh.ch_zone = zone.name
				wh.ch_location_type = "Zone Warehouse"
				wh.insert(ignore_permissions=True)
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

	orphan_bins = frappe.db.sql(
		"""
		SELECT w.name, w.parent_warehouse
		FROM `tabWarehouse` w
		WHERE w.is_group = 0
		  AND (w.ch_zone IS NULL OR w.ch_zone = '')
		  AND w.parent_warehouse IS NOT NULL
		  AND w.parent_warehouse != ''
		""",
		as_dict=True,
	)
	for bin_ in orphan_bins:
		parent = frappe.db.get_value(
			"Warehouse", bin_.parent_warehouse, ["ch_zone", "ch_city"], as_dict=True
		)
		if parent and parent.ch_zone:
			frappe.db.set_value(
				"Warehouse", bin_.name,
				{"ch_zone": parent.ch_zone, "ch_city": parent.ch_city or None},
				update_modified=False,
			)
	frappe.db.commit()



@frappe.whitelist()
def get_company_location_tree(company=None, warehouse_view="all"):
	"""Return Company → City → Zone → Warehouses / Stores / Offices.

	warehouse_view:
	- all: include all warehouses
	- location: only Store/Zone warehouses
	- operational: everything except Store/Zone warehouses
	"""
	companies = {}
	# Load ALL cities (incl. disabled) so zones already linked to a city
	# later disabled by the admin still render with their proper display name.
	state_codes = {
		s.name: s.state_code
		for s in frappe.get_all("CH State", fields=["name", "state_code"])
	}
	city_map = {}
	for c in frappe.get_all("CH City", fields=["name", "city_name", "state"]):
		c.state_code = state_codes.get(c.state) if c.state else None
		city_map[c.name] = c

	zone_filters = {}
	if company:
		zone_filters["company"] = company

	for zone in frappe.get_all("CH Store Zone", filters=zone_filters, fields=["name", "zone_name", "company", "city", "source_warehouse"]):
		company_node = companies.setdefault(zone.company, {"company": zone.company, "cities": {}})
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

	# Pre-fetch CH Store labels so warehouse pills (especially Store Bins) can
	# display the friendly store name instead of the verbose warehouse code.
	# In-memory dict join — cheaper than SQL JOIN since the CH Store master is
	# tiny and we'd otherwise have to denormalise via get_all hacks.
	# We surface `store_name` (long descriptive label, e.g. "Kelambakkam Store")
	# so the JS pill can render "<ch_store> (<ch_store_name>)" on bin pills
	# whenever the short id and long label differ.
	store_labels = {
		s.name: s.store_name
		for s in frappe.get_all("CH Store", fields=["name", "store_name"])
	}

	for warehouse in frappe.get_all(
		"Warehouse",
		filters={"disabled": 0, "is_group": 0},
		fields=["name", "warehouse_name", "company", "ch_city", "ch_zone",
			"ch_location_type", "ch_store", "ch_bin_type"],
	):
		if company and warehouse.company != company:
			continue
		if not _warehouse_matches_view(warehouse, warehouse_view):
			continue
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

	store_filters = {"disabled": 0}
	if company:
		store_filters["company"] = company
	for store in frappe.get_all("CH Store", filters=store_filters, fields=["name", "store_code", "store_name", "company", "city", "zone", "warehouse"]):
		_zone_bucket(companies, store.company, store.city, store.zone)["stores"].append(store)

	for office in frappe.get_all("Branch", fields=["name", "branch", "ch_company", "ch_city", "ch_zone"]):
		office_company = office.ch_company
		if company and office_company != company:
			continue
		if not office_company:
			continue
		_zone_bucket(companies, office_company, office.ch_city, office.ch_zone)["offices"].append(office)

	return _serialize_tree(companies)


def _zone_bucket(companies, company, city, zone):
	company_node = companies.setdefault(company, {"company": company, "cities": {}})
	city_key = city or "Unassigned"
	city_node = company_node["cities"].setdefault(city_key, {"city": city_key, "city_name": city_key, "state": None, "state_code": None, "zones": {}})
	zone_key = zone or "Unassigned"
	return city_node["zones"].setdefault(
		zone_key,
		{"zone": zone_key, "zone_name": zone_key, "source_warehouse": None, "warehouses": [], "stores": [], "offices": []},
	)


def _warehouse_matches_view(warehouse, warehouse_view):
	location_types = {"Store Warehouse", "Zone Warehouse"}
	view = (warehouse_view or "all").strip().lower()
	w_type = (warehouse.ch_location_type or "").strip()

	if view == "location":
		return w_type in location_types
	if view == "operational":
		return w_type not in location_types
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


# ---------------------------------------------------------------------------
# CRUD endpoints powering the Location Hierarchy page
# ---------------------------------------------------------------------------

def _check_master_permission():
	if frappe.session.user == "Administrator":
		return
	roles = set(frappe.get_roles())
	if not roles.intersection({"System Manager", "CH Master Manager"}):
		frappe.throw("You are not allowed to manage the Location Hierarchy.", frappe.PermissionError)


@frappe.whitelist()
def list_companies():
	return frappe.get_all("Company", fields=["name", "company_name"], order_by="company_name")


@frappe.whitelist()
def list_warehouses(company=None, unassigned_only=0):
	filters = {"disabled": 0, "is_group": 0}
	if company:
		filters["company"] = company
	if int(unassigned_only or 0):
		filters["ch_zone"] = ["in", [None, ""]]
	return frappe.get_all(
		"Warehouse",
		filters=filters,
		fields=["name", "warehouse_name", "company", "ch_city", "ch_zone", "ch_location_type"],
		order_by="warehouse_name",
		limit_page_length=0,
	)


@frappe.whitelist()
def list_branches(company=None, unassigned_only=0):
	filters = {}
	if company:
		filters["ch_company"] = company
	if int(unassigned_only or 0):
		filters["ch_zone"] = ["in", [None, ""]]
	return frappe.get_all(
		"Branch",
		filters=filters,
		fields=["name", "branch", "ch_company", "ch_city", "ch_zone"],
		order_by="branch",
		limit_page_length=0,
	)


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


@frappe.whitelist()
def save_city(city_name, state=None, name=None, disabled=0, description=None, company=None):
	"""Create / update a CH City master row.

	``company`` is accepted but NOT persisted — City is a pure geographic
	master (Mumbai is Mumbai across all companies); company association lives
	on CH Store Zone where it belongs. The legacy ``company`` column on
	CH City is kept hidden for backward compatibility but is no longer written.
	``state`` is a Link to CH State; pass the value through unchanged so the
	link key is preserved verbatim (no .title() coercion).
	"""
	_check_master_permission()
	_reject_synthetic(city_name, "city")
	_reject_synthetic(name, "city")
	clean_name = (city_name or "").strip().title()
	clean_state = (state or "").strip() or None

	# City masters are deduplicated by name (autoname = format:{city_name}, so
	# the record name IS the city name — there is no uniqueness suffix). A
	# "create" request for a city that already exists must therefore UPSERT,
	# not blindly insert: otherwise the PRIMARY-key collision surfaces to the
	# user as a raw DuplicateEntryError (e.g. two people both adding
	# "Chennai-33"). When no explicit `name` was supplied, fall back to any
	# existing row whose name already matches the cleaned city name.
	target = name or (clean_name if frappe.db.exists("CH City", clean_name) else None)

	if target:
		doc = frappe.get_doc("CH City", target)
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


@frappe.whitelist()
def delete_city(name):
	_check_master_permission()
	_reject_synthetic(name, "city")
	zones = frappe.db.count("CH Store Zone", {"city": name})
	stores = frappe.db.count("CH Store", {"city": name})
	if zones or stores:
		frappe.throw(f"Cannot delete city '{name}' — {zones} zone(s) and {stores} store(s) are linked.")
	frappe.delete_doc("CH City", name)
	return True


# ---------------------------------------------------------------------------
# CH State master CRUD (admin-only — surfaced from the Location Hierarchy page)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_states():
	"""Return CH States ordered by state_name for picker dialogs."""
	return frappe.get_all(
		"CH State",
		filters={"disabled": 0},
		fields=["name", "state_name", "state_code", "country"],
		order_by="state_name",
	)


@frappe.whitelist()
def save_state(state_name, state_code, country="India", name=None, disabled=0, description=None):
	"""Create / update a CH State row.

	``state_code`` is the GST / ISO 3166-2 code (e.g. KA, MH, 29). It is
	required and must be unique — enforced by the DocType field constraints.
	"""
	_check_master_permission()
	clean_name = (state_name or "").strip().title()
	clean_code = (state_code or "").strip().upper()
	if not clean_name:
		frappe.throw("State Name is required.")
	if not clean_code:
		frappe.throw("State Code is required (e.g. KA, MH, 29).")
	if name:
		doc = frappe.get_doc("CH State", name)
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


@frappe.whitelist()
def delete_state(name):
	_check_master_permission()
	cities = frappe.db.count("CH City", {"state": name})
	if cities:
		frappe.throw(f"Cannot delete state '{name}' — {cities} city(ies) are linked.")
	frappe.delete_doc("CH State", name)
	return True


@frappe.whitelist()
def save_zone(company, city, zone_name, source_warehouse=None, name=None, description=None):
	_check_master_permission()
	_reject_synthetic(zone_name, "zone")
	_reject_synthetic(name, "zone")
	_reject_synthetic(city, "city")
	if name:
		doc = frappe.get_doc("CH Store Zone", name)
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

	# Sync source warehouse tagging
	if doc.source_warehouse and frappe.db.exists("Warehouse", doc.source_warehouse):
		frappe.db.set_value(
			"Warehouse",
			doc.source_warehouse,
			{"ch_city": doc.city, "ch_zone": doc.name, "ch_location_type": "Zone Warehouse"},
			update_modified=False,
		)
	return doc.name


@frappe.whitelist()
def delete_zone(name):
	_check_master_permission()
	_reject_synthetic(name, "zone")
	stores = frappe.db.count("CH Store", {"zone": name})
	whs = frappe.db.count("Warehouse", {"ch_zone": name})
	branches = frappe.db.count("Branch", {"ch_zone": name})
	if stores or whs or branches:
		frappe.throw(
			f"Cannot delete zone '{name}' — {stores} store(s), {whs} warehouse(s), {branches} office(s) are linked."
		)
	frappe.delete_doc("CH Store Zone", name)
	return True


@frappe.whitelist()
def assign_warehouse(warehouse, company=None, city=None, zone=None, location_type=None):
	_check_master_permission()
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(f"Warehouse {warehouse} not found")
	updates = {"ch_city": city or None, "ch_zone": zone or None, "ch_location_type": location_type or None}
	frappe.db.set_value("Warehouse", warehouse, updates)
	# Cascade zone/city to child bins so they don't appear as Unassigned
	children = frappe.get_all(
		"Warehouse",
		filters={"parent_warehouse": warehouse, "is_group": 0},
		fields=["name"],
	)
	for child in children:
		frappe.db.set_value(
			"Warehouse", child.name,
			{"ch_city": city or None, "ch_zone": zone or None},
			update_modified=False,
		)
	return True


@frappe.whitelist()
def unassign_warehouse(warehouse):
	_check_master_permission()
	frappe.db.set_value("Warehouse", warehouse, {"ch_city": None, "ch_zone": None, "ch_location_type": None})
	return True


@frappe.whitelist()
def assign_office(branch, company=None, city=None, zone=None):
	_check_master_permission()
	if not frappe.db.exists("Branch", branch):
		frappe.throw(f"Branch {branch} not found")
	frappe.db.set_value(
		"Branch",
		branch,
		{"ch_company": company or None, "ch_city": city or None, "ch_zone": zone or None},
	)
	return True


@frappe.whitelist()
def unassign_office(branch):
	_check_master_permission()
	frappe.db.set_value("Branch", branch, {"ch_company": None, "ch_city": None, "ch_zone": None})
	return True


@frappe.whitelist()
def create_office(branch, company=None, city=None, zone=None):
	_check_master_permission()
	if frappe.db.exists("Branch", branch):
		frappe.throw(f"Office '{branch}' already exists")
	doc = frappe.new_doc("Branch")
	doc.branch = branch
	doc.ch_company = company or None
	doc.ch_city = city or None
	doc.ch_zone = zone or None
	doc.insert()
	return doc.name


@frappe.whitelist()
def save_store(company, city, zone, store_name, store_code=None, warehouse=None, branch=None, name=None):
	_check_master_permission()
	if name:
		doc = frappe.get_doc("CH Store", name)
	else:
		doc = frappe.new_doc("CH Store")
	doc.company = company
	doc.city = city
	doc.zone = zone
	if store_code:
		doc.store_code = store_code
	doc.store_name = store_name
	if warehouse:
		doc.warehouse = warehouse
	if branch:
		doc.branch = branch
	doc.save() if name else doc.insert()
	return doc.name


@frappe.whitelist()
def delete_store(name):
	_check_master_permission()
	frappe.delete_doc("CH Store", name)
	return True


@frappe.whitelist()
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
	_check_master_permission()

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

	st = frappe.db.get_value(
		"CH Store",
		store,
		["name", "store_code", "company", "city", "zone", "warehouse"],
		as_dict=True,
	)
	if not st:
		frappe.throw(f"CH Store {store} not found.")
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
	wh.insert(ignore_permissions=True)

	return {"warehouse": wh.name, "bin_type": bin_type, "created": True}