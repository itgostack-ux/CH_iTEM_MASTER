import frappe


def ensure_city(company, city_name, state=None):
	if not company or not city_name:
		return None

	clean_city = city_name.strip().title()
	if not clean_city:
		return None

	existing = frappe.db.get_value("CH City", {"company": company, "city_name": clean_city}, "name")
	if existing:
		return existing

	legacy_city = frappe.db.get_value("CH City", clean_city, ["name", "company"], as_dict=True)
	if legacy_city and legacy_city.company == company:
		return legacy_city.name

	city = frappe.new_doc("CH City")
	city.city_name = clean_city
	city.company = company
	city.state = state.strip().title() if state else None
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


@frappe.whitelist()
def get_company_location_tree(company=None):
	"""Return Company → City → Zone → Warehouses / Stores / Offices."""
	company_filters = {"disabled": 0}
	if company:
		company_filters["company"] = company

	companies = {}
	for city in frappe.get_all("CH City", filters=company_filters, fields=["name", "city_name", "company", "state"]):
		company_node = companies.setdefault(city.company, {"company": city.company, "cities": {}})
		company_node["cities"][city.name] = {
			"city": city.name,
			"city_name": city.city_name,
			"state": city.state,
			"zones": {},
		}

	zone_filters = {}
	if company:
		zone_filters["company"] = company

	for zone in frappe.get_all("CH Store Zone", filters=zone_filters, fields=["name", "zone_name", "company", "city", "source_warehouse"]):
		company_node = companies.setdefault(zone.company, {"company": zone.company, "cities": {}})
		city_key = zone.city or "Unassigned"
		city_node = company_node["cities"].setdefault(
			city_key,
			{"city": city_key, "city_name": city_key, "state": None, "zones": {}},
		)
		city_node["zones"][zone.name] = {
			"zone": zone.name,
			"zone_name": zone.zone_name,
			"source_warehouse": zone.source_warehouse,
			"warehouses": [],
			"stores": [],
			"offices": [],
		}

	for warehouse in frappe.get_all(
		"Warehouse",
		filters={"disabled": 0, "is_group": 0},
		fields=["name", "warehouse_name", "company", "ch_city", "ch_zone", "ch_location_type"],
	):
		if company and warehouse.company != company:
			continue
		_zone_bucket(companies, warehouse.company, warehouse.ch_city, warehouse.ch_zone)["warehouses"].append(warehouse)

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
	city_node = company_node["cities"].setdefault(city_key, {"city": city_key, "city_name": city_key, "state": None, "zones": {}})
	zone_key = zone or "Unassigned"
	return city_node["zones"].setdefault(
		zone_key,
		{"zone": zone_key, "zone_name": zone_key, "source_warehouse": None, "warehouses": [], "stores": [], "offices": []},
	)


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


@frappe.whitelist()
def save_city(company, city_name, state=None, name=None, disabled=0, description=None):
	_check_master_permission()
	if name:
		doc = frappe.get_doc("CH City", name)
		doc.city_name = city_name.strip().title()
		doc.company = company
		doc.state = (state or "").strip().title() or None
		doc.disabled = int(disabled or 0)
		doc.description = description
		doc.save()
	else:
		doc = frappe.new_doc("CH City")
		doc.city_name = city_name.strip().title()
		doc.company = company
		doc.state = (state or "").strip().title() or None
		doc.disabled = int(disabled or 0)
		doc.description = description
		doc.insert()
	return doc.name


@frappe.whitelist()
def delete_city(name):
	_check_master_permission()
	zones = frappe.db.count("CH Store Zone", {"city": name})
	stores = frappe.db.count("CH Store", {"city": name})
	if zones or stores:
		frappe.throw(f"Cannot delete city '{name}' — {zones} zone(s) and {stores} store(s) are linked.")
	frappe.delete_doc("CH City", name)
	return True


@frappe.whitelist()
def save_zone(company, city, zone_name, source_warehouse=None, name=None, description=None):
	_check_master_permission()
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