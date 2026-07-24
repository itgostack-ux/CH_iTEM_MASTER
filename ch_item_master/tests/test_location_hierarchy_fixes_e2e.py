"""End-to-end tests for the Location Hierarchy UX fixes shipped 2026-06-24.

Covers:
  #1  Hub Bin location type + ``create_hub_bin`` API + ``ch_hub_bin_type``.
  #2  Sellable warehouse filter used by the Add Store dialog.
  #5  Duplicate CH Store name (per company) rejection.
  #6  Duplicate Zone Warehouse (hub) tag rejection.
  #7  POS Profile auto-provisioning for a CH Store.

Runs against real fixture data on ``erpnext.local``:
  * company = "Bestbuy Mobiles Private Limited"
  * zone    = "Chennai Central" (source_warehouse = "Chennai - Hub - BMPL")
  * store   = "GG-ALWARTHIRUNAGAR" (has warehouse, no pos_profile)

Every test rolls back its side effects on exit. bench execute swallows real
exceptions and surfaces a misleading NameError fallback, so this module uses
try/except with explicit ``print`` diagnostics; ``run()`` returns a summary
dict and re-raises on the first failure.
"""

import traceback

import frappe

from ch_item_master.ch_core import location_hierarchy as lh
from ch_item_master.ch_core.doctype.ch_store import ch_store as ch_store_mod


COMPANY = "Bestbuy Mobiles Private Limited"
ZONE = "Chennai Central"
CITY = "Chennai-33"
STORE_FOR_POS = "GG-ALWARTHIRUNAGAR"
HUB_BIN_LABEL = "E2ETest-Sellable-01"
DUP_STORE_NAME = "E2E Test Duplicate Store"


def _log(step, ok, detail=""):
	tag = "PASS" if ok else "FAIL"
	print(f"[{tag}] {step}: {detail}")


def _delete_warehouse_if_exists(name):
	if name and frappe.db.exists("Warehouse", name):
		try:
			frappe.delete_doc("Warehouse", name, ignore_permissions=True, force=True)
		except Exception:
			# Ignore SLE-linked warehouses (shouldn't happen for fresh test bins).
			pass


def _delete_pos_profile_if_exists(name):
	if name and frappe.db.exists("POS Profile", name):
		try:
			frappe.delete_doc("POS Profile", name, ignore_permissions=True, force=True)
		except Exception:
			pass


def _delete_store_if_exists(name):
	if name and frappe.db.exists("CH Store", name):
		try:
			frappe.delete_doc("CH Store", name, ignore_permissions=True, force=True)
		except Exception:
			pass


# ---------------------------------------------------------------------------
# Test A — POS Profile auto-create (issue #7)
# ---------------------------------------------------------------------------
def test_pos_profile_autocreate():
	store_name = STORE_FOR_POS
	store = frappe.get_doc("CH Store", store_name)
	if not store.warehouse:
		raise AssertionError(f"{store_name} has no warehouse — cannot test POS profile creation.")

	prior_pp = store.pos_profile
	# Force a fresh provision (issue #7 form button path).
	result = ch_store_mod.create_pos_profile_for_store(store_name)
	assert result is not None, "ensure_store_pos_profile returned None"
	pp_name = result.get("pos_profile")
	assert pp_name, f"No pos_profile in result: {result}"
	assert frappe.db.exists("POS Profile", pp_name), f"POS Profile {pp_name} not persisted"

	# Field checks: company and warehouse always match. A newly created
	# skeleton is disabled; an existing operator-activated profile is reused.
	pp = frappe.db.get_value(
		"POS Profile", pp_name,
		["company", "warehouse", "disabled"],
		as_dict=True,
	)
	assert pp.company == COMPANY, f"Wrong company: {pp.company}"
	assert pp.warehouse == store.warehouse, f"Wrong warehouse: {pp.warehouse}"
	if result.get("created"):
		assert pp.disabled == 1, f"POS Profile should be disabled on seed insert, got {pp.disabled}"

	# Link back on CH Store.
	linked = frappe.db.get_value("CH Store", store_name, "pos_profile")
	assert linked == pp_name, f"CH Store.pos_profile not linked (got {linked})"

	# Idempotency: second call reuses.
	result2 = ch_store_mod.create_pos_profile_for_store(store_name)
	assert result2.get("pos_profile") == pp_name, "Second call should reuse profile"
	assert result2.get("created") is False, "Second call should report created=False"

	_log(
		"A. POS Profile auto-create",
		True,
		f"{'created' if result.get('created') else 'reused'} {pp_name}, idempotent on second call",
	)
	return {
		"pos_profile": pp_name,
		"prior_pp": prior_pp,
		"created": bool(result.get("created")),
	}


def _cleanup_pos_profile(state):
	if not state:
		return
	pp_name = state.get("pos_profile")
	prior_pp = state.get("prior_pp")
	# Restore prior link (was likely None) and delete our seed profile.
	if pp_name and STORE_FOR_POS:
		frappe.db.set_value(
			"CH Store", STORE_FOR_POS, "pos_profile", prior_pp,
			update_modified=False,
		)
	if state.get("created"):
		_delete_pos_profile_if_exists(pp_name)


# ---------------------------------------------------------------------------
# Test B — Hub Bin create (issue #1)
# ---------------------------------------------------------------------------
def test_hub_bin_create():
	# Preflight: cleanup a stale test bin from a prior aborted run.
	stale = frappe.db.get_value(
		"Warehouse",
		{
			"ch_zone": ZONE,
			"ch_location_type": "Hub Bin",
			"ch_hub_bin_type": HUB_BIN_LABEL,
		},
		"name",
	)
	_delete_warehouse_if_exists(stale)

	result = lh.create_hub_bin(zone=ZONE, label=HUB_BIN_LABEL)
	assert result.get("created") is True, f"First call should create: {result}"
	wh_name = result["warehouse"]
	assert frappe.db.exists("Warehouse", wh_name), f"Warehouse not persisted: {wh_name}"

	w = frappe.db.get_value(
		"Warehouse", wh_name,
		["company", "is_group", "ch_zone", "ch_location_type",
		 "ch_hub_bin_type", "parent_warehouse"],
		as_dict=True,
	)
	assert w.company == COMPANY, f"Wrong company: {w.company}"
	assert w.is_group == 0, "Hub Bin must be a leaf, not a group"
	assert w.ch_zone == ZONE, f"Wrong zone: {w.ch_zone}"
	assert w.ch_location_type == "Hub Bin", f"Wrong location_type: {w.ch_location_type}"
	assert w.ch_hub_bin_type == HUB_BIN_LABEL, f"Wrong hub_bin_type: {w.ch_hub_bin_type}"

	# Parent must be the Zone Warehouse's parent (Zone Group), NOT the hub leaf.
	hub = frappe.db.get_value("CH Store Zone", ZONE, "source_warehouse")
	hub_parent = frappe.db.get_value("Warehouse", hub, "parent_warehouse") or hub
	assert w.parent_warehouse == hub_parent, (
		f"Hub Bin parent should be {hub_parent} (zone group), got {w.parent_warehouse}"
	)

	# Idempotency: second call must return created=False, same warehouse.
	result2 = lh.create_hub_bin(zone=ZONE, label=HUB_BIN_LABEL)
	assert result2.get("created") is False, f"Second call should be idempotent: {result2}"
	assert result2["warehouse"] == wh_name

	# list_hub_bins surfaces it.
	listing = lh.list_hub_bins(zone=ZONE)
	assert any(row["name"] == wh_name for row in listing), (
		f"list_hub_bins missed the new bin: {[r['name'] for r in listing]}"
	)

	# Sanitizer: invalid label rejected.
	try:
		lh.create_hub_bin(zone=ZONE, label="bad/label!")
	except frappe.ValidationError:
		pass
	else:
		raise AssertionError("Invalid label should have been rejected")

	_log("B. Hub Bin create", True,
	     f"created {wh_name} under {hub_parent}, idempotent, sanitizer works")
	return {"warehouse": wh_name}


def _cleanup_hub_bin(state):
	if not state:
		return
	_delete_warehouse_if_exists(state.get("warehouse"))


# ---------------------------------------------------------------------------
# Test C — Duplicate CH Store name rejected (issue #5)
# ---------------------------------------------------------------------------
def test_duplicate_store_name():
	# Cleanup stale test rows from a prior aborted run.
	for existing in frappe.get_all(
		"CH Store",
		filters={"store_name": DUP_STORE_NAME, "company": COMPANY},
		pluck="name",
	):
		_delete_store_if_exists(existing)

	# Insert first store — should succeed.
	s1 = frappe.new_doc("CH Store")
	s1.store_name = DUP_STORE_NAME
	s1.company = COMPANY
	s1.city = CITY
	s1.zone = ZONE
	s1.flags.ignore_permissions = True
	# after_insert calls ensure_store_bins/ensure_store_pos_profile which
	# both bail out gracefully when warehouse is unset.
	s1.insert(ignore_permissions=True)
	assert frappe.db.exists("CH Store", s1.name), "First insert failed"

	# Insert second store with same name/company — should raise ValidationError.
	s2 = frappe.new_doc("CH Store")
	s2.store_name = DUP_STORE_NAME
	s2.company = COMPANY
	s2.city = CITY
	s2.zone = ZONE
	s2.flags.ignore_permissions = True
	try:
		s2.insert(ignore_permissions=True)
	except frappe.ValidationError as exc:
		msg = str(exc)
		assert DUP_STORE_NAME in msg or "already exists" in msg.lower(), (
			f"Wrong error message: {msg}"
		)
	else:
		raise AssertionError(
			f"Duplicate store name should have been rejected. "
			f"Inserted {s2.name}"
		)

	_log("C. Duplicate store name rejected", True,
	     f"first insert {s1.name} OK, second insert raised ValidationError")
	return {"store": s1.name}


def _cleanup_duplicate_store(state):
	if not state:
		return
	_delete_store_if_exists(state.get("store"))
	# Belt-and-braces: nuke any lingering rows with the test name.
	for existing in frappe.get_all(
		"CH Store",
		filters={"store_name": DUP_STORE_NAME, "company": COMPANY},
		pluck="name",
	):
		_delete_store_if_exists(existing)


# ---------------------------------------------------------------------------
# Test D — Hub source assignment is authoritative
# ---------------------------------------------------------------------------
def test_duplicate_hub_tag():
	hub = frappe.db.get_value("CH Store Zone", ZONE, "source_warehouse")
	assert hub, f"Zone {ZONE} has no source_warehouse"

	orig = frappe.db.get_value(
		"Warehouse", hub,
		["ch_city", "ch_zone", "ch_location_type"],
		as_dict=True,
	)

	# Find a DIFFERENT untagged warehouse in the same company to assign as the
	# temporary source hub. In the hardened model, the zone source field is
	# authoritative, so assigning a clean hub replaces the source instead of
	# relying on duplicate Warehouse.ch_zone tags.
	candidate = frappe.db.sql("""
		SELECT name FROM `tabWarehouse`
		WHERE company = %(c)s AND disabled = 0 AND is_group = 0
		  AND name != %(h)s
		  AND (ch_store IS NULL OR ch_store = '')
		  AND (ch_city IS NULL OR ch_city = '' OR ch_city = %(city)s)
		  AND (ch_bin_type IS NULL OR ch_bin_type = '')
		  AND (ch_location_type IS NULL OR ch_location_type = '')
		LIMIT 1
	""", {"c": COMPANY, "h": hub, "city": CITY}, as_dict=True)
	created_candidate = False
	if candidate:
		candidate_name = candidate[0].name
	else:
		seed_name = "E2E-Hub-Candidate"
		seed_full = f"{seed_name} - BMPL"
		if frappe.db.exists("Warehouse", seed_full):
			frappe.delete_doc("Warehouse", seed_full, ignore_permissions=True, force=True)
		wh = frappe.new_doc("Warehouse")
		wh.warehouse_name = seed_name
		wh.company = COMPANY
		wh.is_group = 0
		wh.insert(ignore_permissions=True)
		candidate_name = wh.name
		created_candidate = True

	cand_orig = frappe.db.get_value(
		"Warehouse", candidate_name,
		["ch_city", "ch_zone", "ch_location_type"],
		as_dict=True,
	)

	lh.assign_warehouse(
		warehouse=candidate_name, company=COMPANY, city=CITY, zone=ZONE,
		location_type="Zone Warehouse",
	)
	linked = frappe.db.get_value("CH Store Zone", ZONE, "source_warehouse")
	assert linked == candidate_name, (
		f"Assign Hub should update CH Store Zone.source_warehouse, got {linked}"
	)
	candidate_type = frappe.db.get_value("Warehouse", candidate_name, "ch_location_type")
	assert candidate_type == "Zone Warehouse", (
		f"Candidate should be tagged Zone Warehouse, got {candidate_type}"
	)

	_log("D. Hub source assignment", True,
	     f"{ZONE}: {hub} -> {candidate_name}")
	return {"hub": hub, "orig": orig, "candidate": candidate_name, "cand_orig": cand_orig,
	        "zone": ZONE, "created_candidate": created_candidate}


def _cleanup_duplicate_hub(state):
	if not state:
		return
	if state.get("zone") and state.get("hub"):
		frappe.db.set_value(
			"CH Store Zone", state["zone"], "source_warehouse", state["hub"],
			update_modified=False,
		)
		lh.sync_zone_source_warehouse_metadata(state["zone"])

	cand_orig = state.get("cand_orig") or {}
	if state.get("candidate"):
		if state.get("created_candidate"):
			_delete_warehouse_if_exists(state["candidate"])
		else:
			frappe.db.set_value(
				"Warehouse", state["candidate"],
				{
					"ch_city": cand_orig.get("ch_city") or None,
					"ch_zone": cand_orig.get("ch_zone") or None,
					"ch_location_type": cand_orig.get("ch_location_type") or None,
				},
				update_modified=False,
			)


# ---------------------------------------------------------------------------
# Test E — Sellable warehouse filter used by Add Store dialog (issue #2)
# ---------------------------------------------------------------------------
def test_sellable_warehouse_filter():
	"""The Add Store Sellable picker excludes every store-owned warehouse."""
	rows = frappe.call(
		"ch_item_master.ch_core.location_hierarchy.sellable_warehouse_query",
		doctype="Warehouse",
		txt="",
		searchfield="name",
		start=0,
		page_len=500,
		filters={"company": COMPANY, "city": CITY, "zone": ZONE},
	)
	names = {row[0] for row in (rows or [])}

	bad = []
	for name in names:
		r = frappe.db.get_value(
			"Warehouse",
			name,
			["ch_city", "ch_store", "ch_bin_type", "ch_location_type"],
			as_dict=True,
		)
		if r.ch_city and r.ch_city != CITY:
			bad.append(f"{name} city={r.ch_city}")
			continue
		if r.ch_bin_type and r.ch_bin_type not in ("Sellable",):
			bad.append(f"{name} bin_type={r.ch_bin_type}")
			continue
		if r.ch_location_type == "Zone Warehouse":
			bad.append(f"{name} is a Zone Warehouse")
			continue
		if r.ch_store:
			bad.append(f"{name} tagged to store {r.ch_store}")
			continue
		assigned_store = frappe.db.get_value(
			"CH Store", {"warehouse": name, "disabled": 0}, "name"
		)
		if assigned_store:
			bad.append(f"{name} assigned to store {assigned_store}")
			continue
	assert not bad, f"Filter leaked non-sellable rows: {bad[:5]}"

	# The dialog also must NOT surface Goods-In-Transit / Buyback bins.
	forbidden = frappe.get_all(
		"Warehouse",
		filters={
			"company": COMPANY,
			"disabled": 0,
			"is_group": 0,
			"ch_bin_type": ["in", ["Buyback"]],
		},
		pluck="name",
	)
	leaked = [n for n in forbidden if n in names]
	assert not leaked, f"Buyback bins leaked into Sellable dialog: {leaked}"

	_log("E. Sellable warehouse filter", True,
	     f"returned {len(names)} candidates, no store-owned/Buyback/hub leakage")


# ---------------------------------------------------------------------------
# Test F — Server-side picker bifurcation (Hub / Store / Other)
# ---------------------------------------------------------------------------
def test_picker_bifurcation():
	"""Every Link-picker query function must reject the wrong warehouse class."""
	# Sample expected rejects.
	store_bin = frappe.db.get_value(
		"Warehouse",
		{"company": COMPANY, "ch_bin_type": "Buyback", "ch_store": ["is", "set"]},
		"name",
	)
	store_sellable = frappe.db.get_value(
		"Warehouse",
		{"company": COMPANY, "ch_bin_type": "Sellable", "ch_store": ["is", "set"]},
		"name",
	)
	transit = frappe.db.get_value(
		"Warehouse",
		{"company": COMPANY, "warehouse_type": "Transit", "is_group": 0},
		"name",
	)
	hub_incumbent = frappe.db.get_value(
		"Warehouse",
		{"company": COMPANY, "ch_location_type": "Zone Warehouse", "is_group": 0,
		 "ch_store": ["is", "not set"]},
		"name",
	)

	# Seed an untagged blank warehouse so the picker has at least one positive
	# candidate on this dev site (every legacy warehouse is already zone-tagged).
	seed_name = "E2E-Bifurcation-Blank"
	seed_full = f"{seed_name} - BMPL"
	if not frappe.db.exists("Warehouse", seed_full):
		seed_wh = frappe.new_doc("Warehouse")
		seed_wh.warehouse_name = seed_name
		seed_wh.company = COMPANY
		seed_wh.is_group = 0
		seed_wh.insert(ignore_permissions=True)
		seed_full = seed_wh.name

	def call_query(fn_name, extra=None):
		filters = {"company": COMPANY}
		if extra:
			filters.update(extra)
		rows = frappe.call(
			fn_name,
			doctype="Warehouse",
			txt="",
			searchfield="name",
			start=0,
			page_len=500,
			filters=filters,
		)
		return {r[0] for r in (rows or [])}

	try:
		# --- Hub picker ---
		hub_names = call_query(
			"ch_item_master.ch_core.location_hierarchy.hub_warehouse_query",
			{"city": CITY, "zone": ZONE},
		)
		if store_bin:
			assert store_bin not in hub_names, f"Hub picker leaked store bin {store_bin}"
		if store_sellable:
			assert store_sellable not in hub_names, (
				f"Hub picker leaked store sellable {store_sellable}"
			)
		if transit:
			assert transit not in hub_names, f"Hub picker leaked transit warehouse {transit}"
		assert seed_full in hub_names, (
			f"Hub picker missed the untagged seed warehouse {seed_full}"
		)

		# --- Other picker ---
		other_names = call_query(
			"ch_item_master.ch_core.location_hierarchy.other_warehouse_query",
		)
		if store_bin:
			assert store_bin not in other_names, f"Other picker leaked store bin {store_bin}"
		if store_sellable:
			assert store_sellable not in other_names, (
				f"Other picker leaked store sellable {store_sellable}"
			)
		if hub_incumbent:
			assert hub_incumbent not in other_names, (
				f"Other picker leaked hub {hub_incumbent}"
			)
		assert seed_full in other_names, (
			f"Other picker missed the untagged seed warehouse {seed_full}"
		)

		# --- Sellable picker ---
		sellable_names = call_query(
			"ch_item_master.ch_core.location_hierarchy.sellable_warehouse_query",
			{"city": CITY, "zone": ZONE},
		)
		if store_bin:
			assert store_bin not in sellable_names, (
				f"Sellable picker leaked store bin {store_bin}"
			)
		if store_sellable:
			assert store_sellable not in sellable_names, (
				f"Sellable picker leaked store sellable {store_sellable}"
			)
		if transit:
			assert transit not in sellable_names, (
				f"Sellable picker leaked transit warehouse {transit}"
			)
		if hub_incumbent:
			assert hub_incumbent not in sellable_names, (
				f"Sellable picker leaked hub {hub_incumbent}"
			)
		assert seed_full in sellable_names, (
			f"Sellable picker missed the untagged seed warehouse {seed_full}"
		)

		_log("F. Picker bifurcation", True,
		     f"hub={len(hub_names)} other={len(other_names)} sellable={len(sellable_names)}; "
		     f"seed {seed_full} appears in all three; no cross-leaks "
		     f"(store sellables/bins, hubs, and transit filtered correctly)")
	finally:
		# Cleanup seed warehouse
		if frappe.db.exists("Warehouse", seed_full):
			try:
				frappe.delete_doc("Warehouse", seed_full, ignore_permissions=True, force=True)
			except Exception:
				pass


# ---------------------------------------------------------------------------
# Test G — Retail location integrity hardening
# ---------------------------------------------------------------------------
def test_retail_location_integrity():
	"""Hub/store contracts match the SAP/Oracle-style hierarchy invariants."""
	result = lh.repair_retail_location_integrity(COMPANY)
	assert isinstance(result, dict), f"repair returned unexpected result: {result}"

	zones = frappe.get_all(
		"CH Store Zone",
		filters={"company": COMPANY},
		fields=["name", "city", "source_warehouse"],
		order_by="name",
	)
	assert zones, f"No CH Store Zones found for {COMPANY}"

	source_counts = {}
	source_cities = {}
	bad = []
	for z in zones:
		if not z.source_warehouse:
			bad.append(f"{z.name}: missing source_warehouse")
			continue
		source_counts[z.source_warehouse] = source_counts.get(z.source_warehouse, 0) + 1
		source_cities.setdefault(z.source_warehouse, set()).add(z.city)
		wh = frappe.db.get_value(
			"Warehouse",
			z.source_warehouse,
			["company", "is_group", "ch_city", "ch_location_type", "ch_store", "ch_bin_type"],
			as_dict=True,
		)
		if not wh:
			bad.append(f"{z.name}: source {z.source_warehouse} does not exist")
			continue
		if wh.company != COMPANY:
			bad.append(f"{z.name}: source company {wh.company}")
		if wh.is_group:
			bad.append(f"{z.name}: source {z.source_warehouse} is a group")
		if wh.ch_location_type != "Zone Warehouse":
			bad.append(f"{z.name}: source type {wh.ch_location_type}")
		if wh.ch_city and wh.ch_city != z.city:
			bad.append(f"{z.name}: source city {wh.ch_city}, zone city {z.city}")
		if wh.ch_store:
			bad.append(f"{z.name}: source is store-owned {wh.ch_store}")
		if wh.ch_bin_type:
			bad.append(f"{z.name}: source has store bin type {wh.ch_bin_type}")
	for source, cities in source_cities.items():
		clean_cities = {c for c in cities if c}
		if len(clean_cities) > 1:
			bad.append(f"{source}: shared across cities {sorted(clean_cities)}")
	assert not bad, "Invalid zone source warehouses:\n" + "\n".join(bad)

	for source, count in source_counts.items():
		if count <= 1:
			continue
		ch_zone = frappe.db.get_value("Warehouse", source, "ch_zone")
		assert not ch_zone, (
			f"Shared hub {source} is referenced by {count} zones but still "
			f"carries Warehouse.ch_zone={ch_zone}"
		)

	tree = lh.get_company_location_tree(COMPANY, warehouse_view="location")
	zone_buckets = {}
	city_hubs = {}
	for company_node in tree:
		for city in company_node.get("cities") or []:
			city_hubs[city.get("city")] = {
				entry.get("warehouse", {}).get("name")
				for entry in city.get("hubs") or []
			}
			for zone in city.get("zones") or []:
				zone_buckets[zone["zone"]] = (city.get("city"), zone)

	missing_in_tree = []
	for z in zones:
		entry = zone_buckets.get(z.name)
		if not entry:
			missing_in_tree.append(f"{z.name}: no tree bucket")
			continue
		city_name, bucket = entry
		names = {w.get("name") for w in bucket.get("warehouses") or []}
		if z.source_warehouse not in names and z.source_warehouse not in city_hubs.get(city_name, set()):
			missing_in_tree.append(
				f"{z.name}: source {z.source_warehouse} not rendered in its city or zone"
			)
	assert not missing_in_tree, "Source hubs missing from Location Hierarchy tree:\n" + "\n".join(missing_in_tree)

	from ch_erp15.ch_erp15.store_request_api import _get_zone_source_warehouse

	store = frappe.db.get_value(
		"CH Store",
		{"company": COMPANY, "zone": ZONE, "disabled": 0},
		"name",
	)
	assert store, f"No active store found in {ZONE}"
	expected_source = frappe.db.get_value("CH Store Zone", ZONE, "source_warehouse")
	assert _get_zone_source_warehouse(store) == expected_source, (
		f"Store {store} should resolve source {expected_source}"
	)

	other_zone = frappe.db.get_value(
		"CH Store Zone",
		{"company": COMPANY, "city": ["!=", CITY]},
		["name", "city"],
		as_dict=True,
	)
	if other_zone:
		other_city = other_zone.city
		cross_city_picker = frappe.call(
			"ch_item_master.ch_core.location_hierarchy.hub_warehouse_query",
			doctype="Warehouse",
			txt="",
			searchfield="name",
			start=0,
			page_len=500,
			filters={"company": COMPANY, "city": other_city, "zone": other_zone.name},
		)
		cross_city_names = {row[0] for row in (cross_city_picker or [])}
		assert expected_source not in cross_city_names, (
			f"Hub picker leaked {expected_source} from {CITY} into {other_city}"
		)

		temp_zone = "E2E Cross City Hub Zone"
		if frappe.db.exists("CH Store Zone", temp_zone):
			frappe.delete_doc("CH Store Zone", temp_zone, force=True, ignore_permissions=True)
		try:
			lh.save_zone(
				company=COMPANY,
				city=other_city,
				zone_name=temp_zone,
				source_warehouse=expected_source,
			)
		except frappe.ValidationError:
			pass
		else:
			raise AssertionError(
				f"Hub {expected_source} from {CITY} should be rejected for {other_city}"
			)
		finally:
			if frappe.db.exists("CH Store Zone", temp_zone):
				frappe.delete_doc("CH Store Zone", temp_zone, force=True, ignore_permissions=True)

	# Incomplete legacy/test stores must not silently route through an arbitrary
	# company zone. Operators should classify or disable them explicitly.
	unzoned = frappe.db.get_value(
		"CH Store",
		{"company": COMPANY, "zone": ["is", "not set"], "disabled": 0},
		"name",
	)
	if unzoned:
		assert _get_zone_source_warehouse(unzoned) is None, (
			f"Unzoned store {unzoned} should not get a fallback source warehouse"
		)

	store_sellable = frappe.db.get_value(
		"Warehouse",
		{"company": COMPANY, "ch_store": ["is", "set"], "ch_bin_type": "Sellable"},
		"name",
	)
	if store_sellable:
		temp_zone = "E2E Invalid Hub Zone"
		if frappe.db.exists("CH Store Zone", temp_zone):
			frappe.delete_doc("CH Store Zone", temp_zone, force=True, ignore_permissions=True)
		try:
			lh.save_zone(
				company=COMPANY,
				city=CITY,
				zone_name=temp_zone,
				source_warehouse=store_sellable,
			)
		except frappe.ValidationError:
			pass
		else:
			raise AssertionError(
				f"Store sellable warehouse {store_sellable} should be rejected as a hub"
			)
		finally:
			if frappe.db.exists("CH Store Zone", temp_zone):
				frappe.delete_doc("CH Store Zone", temp_zone, force=True, ignore_permissions=True)

	_log(
		"G. Retail location integrity",
		True,
		f"zones={len(zones)}, fixed={len(result.get('fixed') or [])}, "
		f"warnings={len(result.get('warnings') or [])}",
	)


# ---------------------------------------------------------------------------
# Test H — Store Sellable warehouse auto-provisioning
# ---------------------------------------------------------------------------
def test_store_warehouse_auto_provision():
	store_names = [
		"E2E Auto Warehouse Store",
		"E2E Reuse Warehouse Store",
	]
	leftover_stores = []
	for store_name in store_names:
		leftover_stores.extend(frappe.get_all(
			"CH Store",
			filters={"company": COMPANY, "store_name": store_name},
			pluck="name",
		))
	_cleanup_auto_store_warehouse({"stores": leftover_stores})

	created_stores = []
	try:
		store = lh.save_store(
			company=COMPANY,
			city=CITY,
			zone=ZONE,
			store_name=store_names[0],
		)
		created_stores.append(store)

		store_doc = frappe.db.get_value(
			"CH Store",
			store,
			["warehouse", "warehouse_group", "pos_profile"],
			as_dict=True,
		)
		assert store_doc.warehouse, "Store warehouse was not auto-created"
		assert store_doc.warehouse_group, "Store warehouse group was not auto-created"
		meta = frappe.db.get_value(
			"Warehouse",
			store_doc.warehouse,
			["ch_city", "ch_zone", "ch_location_type", "ch_store", "ch_bin_type"],
			as_dict=True,
		)
		assert meta.ch_city == CITY, f"auto warehouse city={meta.ch_city}"
		assert meta.ch_zone == ZONE, f"auto warehouse zone={meta.ch_zone}"
		assert meta.ch_location_type == "Store Bin", (
			f"auto warehouse type={meta.ch_location_type}"
		)
		assert meta.ch_store == store, f"auto warehouse store={meta.ch_store}"
		assert meta.ch_bin_type == "Sellable", (
			f"auto warehouse bin type={meta.ch_bin_type}"
		)

		from ch_item_master.ch_core.bin_transfer import get_store_bin

		assert get_store_bin(store, "Sellable") == store_doc.warehouse

		picker_rows = frappe.call(
			"ch_item_master.ch_core.location_hierarchy.sellable_warehouse_query",
			doctype="Warehouse",
			txt="",
			searchfield="name",
			start=0,
			page_len=500,
			filters={"company": COMPANY, "city": CITY, "zone": ZONE},
		)
		picker_names = {row[0] for row in (picker_rows or [])}
		assert store_doc.warehouse not in picker_names, (
			f"Add Store picker leaked assigned Sellable warehouse {store_doc.warehouse}"
		)

		try:
			duplicate = lh.save_store(
				company=COMPANY,
				city=CITY,
				zone=ZONE,
				store_name=store_names[1],
				warehouse=store_doc.warehouse,
			)
		except frappe.ValidationError:
			pass
		else:
			created_stores.append(duplicate)
			raise AssertionError(
				f"Store warehouse {store_doc.warehouse} should not be reusable"
			)

		_log(
			"H. Store warehouse auto-provision",
			True,
			f"{store} -> {store_doc.warehouse}; reuse blocked",
		)
		return {"stores": created_stores}
	except Exception:
		_cleanup_auto_store_warehouse({"stores": created_stores})
		raise


def _cleanup_auto_store_warehouse(state):
	if not state:
		return
	stores = state.get("stores") or []
	groups = []
	for store in stores:
		if not store or not frappe.db.exists("CH Store", store):
			continue
		store_doc = frappe.db.get_value(
			"CH Store", store, ["pos_profile", "warehouse_group"], as_dict=True
		)
		if store_doc and store_doc.pos_profile:
			frappe.db.set_value("CH Store", store, "pos_profile", None, update_modified=False)
			_delete_pos_profile_if_exists(store_doc.pos_profile)
		if store_doc and store_doc.warehouse_group:
			groups.append(store_doc.warehouse_group)
		for bin_wh in frappe.get_all("Warehouse", filters={"ch_store": store}, pluck="name"):
			_delete_warehouse_if_exists(bin_wh)
		_delete_store_if_exists(store)

	for group in groups:
		_delete_warehouse_if_exists(group)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run():
	summary = {"passed": [], "failed": [], "cleanup_errors": []}
	state = {}

	tests = [
		("A_pos_profile", test_pos_profile_autocreate, _cleanup_pos_profile),
		("B_hub_bin", test_hub_bin_create, _cleanup_hub_bin),
		("C_duplicate_store", test_duplicate_store_name, _cleanup_duplicate_store),
		("D_duplicate_hub", test_duplicate_hub_tag, _cleanup_duplicate_hub),
		("E_sellable_filter", test_sellable_warehouse_filter, None),
		("F_picker_bifurcation", test_picker_bifurcation, None),
		("G_retail_location_integrity", test_retail_location_integrity, None),
		("H_store_warehouse_auto_provision", test_store_warehouse_auto_provision, _cleanup_auto_store_warehouse),
	]

	first_failure = None
	for key, fn, cleanup in tests:
		try:
			state[key] = fn() or {}
			summary["passed"].append(key)
		except Exception as exc:
			print(f"[FAIL] {key}: {type(exc).__name__}: {exc}")
			traceback.print_exc()
			summary["failed"].append({"test": key, "error": f"{type(exc).__name__}: {exc}"})
			if first_failure is None:
				first_failure = exc
		finally:
			if cleanup:
				try:
					cleanup(state.get(key))
				except Exception as ce:
					print(f"[WARN] cleanup {key} failed: {ce}")
					summary["cleanup_errors"].append({"test": key, "error": str(ce)})

	frappe.db.commit()

	print("\n===== SUMMARY =====")
	print(f"Passed: {summary['passed']}")
	print(f"Failed: {summary['failed']}")
	if summary["cleanup_errors"]:
		print(f"Cleanup errors: {summary['cleanup_errors']}")
	print("===================\n")

	if first_failure is not None:
		raise first_failure
	return summary
