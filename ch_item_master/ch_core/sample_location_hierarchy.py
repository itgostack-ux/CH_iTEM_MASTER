"""Seed a sample Country → State → City → Zone → Store → 5-bin hierarchy.

Run via:
    bench --site <site> execute ch_item_master.ch_core.sample_location_hierarchy.seed_sample

Idempotent: skips records that already exist.
"""

import frappe


SAMPLE = {
	"country": "India",
	"state": "Tamil Nadu",
	"city_name": "Chennai",
	"zone_name": "Chennai South",
	"zone_warehouse": "Chennai-Hub",
	"store_name": "Marina Beach Store",
	"store_warehouse": "Marina-Beach",
	"contact_phone": "+919876543210",
	"pincode": "600001",
}


def _get_default_company():
	company = frappe.defaults.get_global_default("company")
	if not company:
		companies = frappe.get_all("Company", limit=1, pluck="name")
		company = companies[0] if companies else None
	if not company:
		frappe.throw("No Company found. Create a Company first.")
	return company


def _ensure_warehouse(name, company, is_group=0, parent=None):
	# Frappe scopes warehouse names per company via "<name> - <abbr>" suffix.
	abbr = frappe.db.get_value("Company", company, "abbr") or ""
	full_name = f"{name} - {abbr}" if abbr else name
	if frappe.db.exists("Warehouse", full_name):
		return full_name
	wh = frappe.new_doc("Warehouse")
	wh.warehouse_name = name
	wh.company = company
	wh.is_group = is_group
	if parent:
		wh.parent_warehouse = parent
	wh.insert(ignore_permissions=True)
	return wh.name


@frappe.whitelist()
def seed_sample():
	"""Create a complete sample location hierarchy with 5 bins."""
	company = _get_default_company()
	out = {"company": company, "created": [], "skipped": []}

	# 1. City (state stored on City)
	city_name = SAMPLE["city_name"]
	existing_city = frappe.db.get_value(
		"CH City", {"company": company, "city_name": city_name}, "name"
	)
	if existing_city:
		city = existing_city
		out["skipped"].append(f"CH City: {city}")
	else:
		c = frappe.new_doc("CH City")
		c.city_name = city_name
		c.company = company
		c.state = SAMPLE["state"]
		c.insert(ignore_permissions=True)
		city = c.name
		out["created"].append(f"CH City: {city}")

	# 2. Zone Warehouse (group warehouse for the hub)
	zone_wh = _ensure_warehouse(SAMPLE["zone_warehouse"], company, is_group=1)
	out["created"].append(f"Zone Warehouse: {zone_wh}")
	frappe.db.set_value(
		"Warehouse",
		zone_wh,
		{
			"ch_city": city,
			"ch_location_type": "Zone Warehouse",
		},
		update_modified=False,
	)

	# 3. Zone
	existing_zone = frappe.db.get_value(
		"CH Store Zone",
		{"company": company, "city": city, "zone_name": SAMPLE["zone_name"]},
		"name",
	)
	if existing_zone:
		zone = existing_zone
		out["skipped"].append(f"CH Store Zone: {zone}")
	else:
		z = frappe.new_doc("CH Store Zone")
		z.zone_name = SAMPLE["zone_name"]
		z.company = company
		z.city = city
		z.source_warehouse = zone_wh
		z.insert(ignore_permissions=True)
		zone = z.name
		out["created"].append(f"CH Store Zone: {zone}")

	# Tag zone warehouse with zone link.
	frappe.db.set_value("Warehouse", zone_wh, "ch_zone", zone, update_modified=False)

	# 4. Store Warehouse (group so it can hold the 5 bins)
	store_base_wh = _ensure_warehouse(
		SAMPLE["store_warehouse"], company, is_group=1, parent=zone_wh
	)
	out["created"].append(f"Store Warehouse: {store_base_wh}")

	# 5. Store (after_insert creates the 5 bins automatically)
	existing_store = frappe.db.get_value(
		"CH Store",
		{"company": company, "store_name": SAMPLE["store_name"]},
		"name",
	)
	if existing_store:
		store = existing_store
		out["skipped"].append(f"CH Store: {store}")
		# Re-run bin creation idempotently
		from ch_item_master.ch_core.doctype.ch_store.ch_store import ensure_store_bins
		ensure_store_bins(frappe.get_doc("CH Store", store))
	else:
		s = frappe.new_doc("CH Store")
		s.store_name = SAMPLE["store_name"]
		s.company = company
		s.city = city
		s.zone = zone
		s.warehouse = store_base_wh
		s.contact_phone = SAMPLE["contact_phone"]
		s.pincode = SAMPLE["pincode"]
		s.insert(ignore_permissions=True)
		store = s.name
		out["created"].append(f"CH Store: {store}")

	# 6. Verify 5 bins were created
	bins = frappe.get_all(
		"Warehouse",
		filters={"parent_warehouse": store_base_wh, "ch_bin_type": ["!=", ""]},
		fields=["name", "ch_bin_type"],
	)
	out["bins"] = {b.ch_bin_type: b.name for b in bins}

	frappe.db.commit()
	return out
