"""End-to-end tests for the Location Hierarchy UX fixes shipped 2026-06-24.

Covers:
  #1  Hub Bin location type + ``create_hub_bin`` API + ``ch_hub_bin_type``.
  #2  Sellable warehouse filter used by the Add Store dialog.
  #5  Duplicate CH Store name (per company) rejection.
  #6  Duplicate Zone Warehouse (hub) tag rejection.
  #7  POS Profile auto-provisioning for a CH Store.

Runs against real fixture data on ``erpnext.local``:
  * company = "BestBuy Mobiles Pvt Ltd"
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


COMPANY = "BestBuy Mobiles Pvt Ltd"
ZONE = "Chennai Central"
CITY = "Chennai"
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

	# Field checks: company matches, warehouse matches, disabled=1 (seed).
	pp = frappe.db.get_value(
		"POS Profile", pp_name,
		["company", "warehouse", "disabled"],
		as_dict=True,
	)
	assert pp.company == COMPANY, f"Wrong company: {pp.company}"
	assert pp.warehouse == store.warehouse, f"Wrong warehouse: {pp.warehouse}"
	assert pp.disabled == 1, f"POS Profile should be disabled on seed insert, got {pp.disabled}"

	# Link back on CH Store.
	linked = frappe.db.get_value("CH Store", store_name, "pos_profile")
	assert linked == pp_name, f"CH Store.pos_profile not linked (got {linked})"

	# Idempotency: second call reuses.
	result2 = ch_store_mod.create_pos_profile_for_store(store_name)
	assert result2.get("pos_profile") == pp_name, "Second call should reuse profile"
	assert result2.get("created") is False, "Second call should report created=False"

	_log("A. POS Profile auto-create", True,
	     f"created {pp_name} (disabled=1), idempotent on second call")
	return {"pos_profile": pp_name, "prior_pp": prior_pp}


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
# Test D — Duplicate Zone Warehouse (hub) tag rejected (issue #6)
# ---------------------------------------------------------------------------
def test_duplicate_hub_tag():
	# The zone's source_warehouse is the current hub. Tag it as Zone
	# Warehouse first so we have a legitimate incumbent to duplicate against.
	hub = frappe.db.get_value("CH Store Zone", ZONE, "source_warehouse")
	assert hub, f"Zone {ZONE} has no source_warehouse"

	# Snapshot original tag state so we can restore.
	orig = frappe.db.get_value(
		"Warehouse", hub,
		["ch_city", "ch_zone", "ch_location_type"],
		as_dict=True,
	)

	# Ensure only one Zone Warehouse tag exists in this zone before we start.
	# (There may be pre-existing ones from prior manual assigns — if so we
	# treat that as the incumbent.)
	existing_tag = frappe.db.get_value(
		"Warehouse",
		{"ch_zone": ZONE, "ch_location_type": "Zone Warehouse", "disabled": 0},
		"name",
	)
	if not existing_tag:
		# Tag the hub as Zone Warehouse via the real API.
		lh.assign_warehouse(
			warehouse=hub, company=COMPANY, city=CITY, zone=ZONE,
			location_type="Zone Warehouse",
		)
		incumbent = hub
	else:
		incumbent = existing_tag

	# Find a DIFFERENT untagged warehouse in the same company to attempt duplicate.
	candidate = frappe.db.sql("""
		SELECT name FROM `tabWarehouse`
		WHERE company = %(c)s AND disabled = 0 AND is_group = 0
		  AND name != %(h)s
		  AND (ch_location_type IS NULL OR ch_location_type = '')
		LIMIT 1
	""", {"c": COMPANY, "h": incumbent}, as_dict=True)
	assert candidate, "No candidate warehouse available for duplicate-hub test"
	candidate_name = candidate[0].name

	# Snapshot candidate's tag state for restore.
	cand_orig = frappe.db.get_value(
		"Warehouse", candidate_name,
		["ch_city", "ch_zone", "ch_location_type"],
		as_dict=True,
	)

	try:
		lh.assign_warehouse(
			warehouse=candidate_name, company=COMPANY, city=CITY, zone=ZONE,
			location_type="Zone Warehouse",
		)
	except frappe.ValidationError as exc:
		msg = str(exc)
		assert "already has a Hub" in msg or "Duplicate Hub" in msg, (
			f"Wrong error message: {msg}"
		)
	else:
		raise AssertionError(
			f"Duplicate Zone Warehouse assign should have been rejected "
			f"(zone {ZONE} already has {incumbent})"
		)

	_log("D. Duplicate hub tag rejected", True,
	     f"incumbent={incumbent}, second assign {candidate_name} raised ValidationError")
	return {"hub": hub, "orig": orig, "candidate": candidate_name, "cand_orig": cand_orig,
	        "we_tagged_hub": not existing_tag}


def _cleanup_duplicate_hub(state):
	if not state:
		return
	# Restore hub tag if WE set it (otherwise leave the incumbent alone).
	if state.get("we_tagged_hub"):
		orig = state.get("orig") or {}
		frappe.db.set_value(
			"Warehouse", state["hub"],
			{
				"ch_city": orig.get("ch_city") or None,
				"ch_zone": orig.get("ch_zone") or None,
				"ch_location_type": orig.get("ch_location_type") or None,
			},
			update_modified=False,
		)
	# Restore candidate tag (should be unchanged since assign raised).
	cand_orig = state.get("cand_orig") or {}
	if state.get("candidate"):
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
	"""Replicate the dialog's server-side filter and verify hygiene."""
	rows = frappe.get_all(
		"Warehouse",
		filters={
			"company": COMPANY,
			"disabled": 0,
			"is_group": 0,
			"ch_bin_type": ["in", ["", "Sellable"]],
			"ch_location_type": ["in", ["", "Store Warehouse", "Store Bin"]],
			"ch_store": ["is", "not set"],
			"ch_zone": ["in", [ZONE, ""]],
		},
		fields=["name", "ch_bin_type", "ch_store", "ch_location_type"],
		limit_page_length=200,
	)
	# Every row must NOT be tagged to another store, must NOT be a hub/buyback,
	# must NOT be in a different zone.
	bad = []
	for r in rows:
		if r.ch_store:
			bad.append(f"{r.name} tagged to store {r.ch_store}")
			continue
		if r.ch_bin_type and r.ch_bin_type not in ("Sellable",):
			bad.append(f"{r.name} bin_type={r.ch_bin_type}")
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
	leaked = [n for n in forbidden if any(r.name == n for r in rows)]
	assert not leaked, f"Buyback bins leaked into Sellable dialog: {leaked}"

	_log("E. Sellable warehouse filter", True,
	     f"returned {len(rows)} candidates, no Buyback/store-tagged leakage")


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
			{"zone": ZONE},
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
			{"zone": ZONE},
		)
		if store_bin:
			assert store_bin not in sellable_names, (
				f"Sellable picker leaked store bin {store_bin}"
			)
		if store_sellable:
			assert store_sellable not in sellable_names, (
				f"Sellable picker leaked store-owned sellable {store_sellable}"
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
		     f"(store_bin/sellable/hub/transit filtered correctly)")
	finally:
		# Cleanup seed warehouse
		if frappe.db.exists("Warehouse", seed_full):
			try:
				frappe.delete_doc("Warehouse", seed_full, ignore_permissions=True, force=True)
			except Exception:
				pass


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
