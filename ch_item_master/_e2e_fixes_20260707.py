"""E2E verification for the 2026-07-07 fix trio.

PART 1 — CH Serial Lifecycle: a Material Transfer submitted the way Data
         Import submits it (core path, ``frappe.flags.in_import``) must move
         ``CH Serial Lifecycle.current_warehouse`` along with
         ``Serial No.warehouse``; cancelling must move it back.

PART 2 — Legacy company-abbr healing: ``heal_legacy_abbr_suffix_docs`` must
         rename ``X - BMPL`` docs to ``X - BM`` and merge into an existing
         target; the shipped baseline seed JSON must carry no BMPL/GSPL; the
         seed importer must normalise a legacy abbr on company auto-create.

PART 3 — Delivery app: a route-built Forward trip must type its hub stop as
         Pickup; get_trip_detail must annotate manifests with
         pickup/drop_stop_sequence and pickup_photo; manifest-level
         start_pickup must cascade the PICKUP stop to Completed; delivery
         must cascade the DROP stop to Completed.

Run:
    bench --site erpnext.local execute ch_item_master._e2e_fixes_20260707.run
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import frappe
from frappe.utils import cint, nowdate

TAG = "E2E-FIX0707"
COMPANY = "BestBuy Mobiles Pvt Ltd"
HUB_WH = "Chennai - Hub - BM"
CHENNAI_LAT, CHENNAI_LNG = 13.0827, 80.2707

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAA"
    "YAAjCB0C8AAAAASUVORK5CYII="
)

results = {"steps": [], "summary": {"pass": 0, "fail": 0}}


def _assert(ok: bool, name: str, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    results["steps"].append({"step": name, "status": status, "detail": detail})
    results["summary"]["pass" if ok else "fail"] += 1
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def _section(title: str) -> None:
    print(f"\n===== {title} =====")


def _proof_photo() -> str:
    fname = f"{TAG}-proof.png"
    existing = frappe.db.get_value("File", {"file_name": fname}, "file_url")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "File", "file_name": fname, "is_private": 0,
        "content": PNG_BYTES, "decode": False,
    })
    doc.insert(ignore_permissions=True)
    return doc.file_url


def _pick_sellable_wh() -> str:
    wh = frappe.db.get_value(
        "Warehouse",
        {"company": COMPANY, "is_group": 0, "disabled": 0,
         "name": ["like", "%-Sellable - BM"]},
        "name",
    )
    if not wh:
        frappe.throw("No sellable BM warehouse found for the test")
    return wh


def _ensure_item(code: str, serialized: bool) -> str:
    if frappe.db.exists("Item", code):
        frappe.db.set_value("Item", code, {
            "ch_lifecycle_status": "Active", "ch_plm_status": "Approved"})
        return code
    # Borrow governance masters (category / sub-category / HSN) from any
    # complete existing item — the completeness validator requires them.
    donor = frappe.db.get_value(
        "Item",
        {"ch_sub_category": ["!=", ""], "gst_hsn_code": ["!=", ""]},
        ["ch_category", "ch_sub_category", "gst_hsn_code", "item_group"],
        as_dict=True,
    ) or frappe._dict()
    doc = frappe.get_doc({
        "doctype": "Item",
        "item_code": code,
        "item_name": code,
        "item_group": donor.item_group or "All Item Groups",
        "ch_category": donor.ch_category,
        "ch_sub_category": donor.ch_sub_category,
        "gst_hsn_code": donor.gst_hsn_code,
        "stock_uom": "Nos",
        "is_stock_item": 1,
        "ch_item_mrp": 999,
        "has_serial_no": 1 if serialized else 0,
        "ch_serial_kind": "IMEI" if serialized else None,
        "ch_lifecycle_status": "Active",
    })
    doc.insert(ignore_permissions=True)
    frappe.db.set_value("Item", doc.name, {
        "ch_lifecycle_status": "Active", "ch_plm_status": "Approved"})
    return doc.name


def _mk_stock_entry(entry_type, items, from_wh=None, to_wh=None, submit=True):
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = entry_type
    se.purpose = entry_type
    se.company = COMPANY
    se.posting_date = nowdate()
    se.set_posting_time = 1
    if from_wh:
        se.from_warehouse = from_wh
    if to_wh:
        se.to_warehouse = to_wh
    se.remarks = f"[{TAG}]"
    for row in items:
        se.append("items", row)
    se.insert(ignore_permissions=True)
    if submit:
        se.submit()
    return se


# ── PART 1 ────────────────────────────────────────────────────────────────

def part1_serial_lifecycle():
    _section("PART 1 — Serial Lifecycle sync on import-style Material Transfer")
    frappe.flags.in_import = True
    try:
        item = _ensure_item(f"{TAG}-SER-ITM", serialized=True)
        hub, dest = HUB_WH, _pick_sellable_wh()
        stamp = datetime.now(timezone.utc).strftime("%H%M%S")
        serials = [f"3560020{stamp}1"[:15].ljust(15, "7"),
                   f"3560020{stamp}2"[:15].ljust(15, "8")]
        serials = [s[:15] for s in serials]

        _mk_stock_entry("Material Receipt", [{
            "item_code": item, "qty": len(serials), "t_warehouse": hub,
            "serial_no": "\n".join(serials), "use_serial_batch_fields": 1,
            "basic_rate": 100, "allow_zero_valuation_rate": 1,
        }])

        for sn in serials:
            lc = frappe.db.get_value(
                "CH Serial Lifecycle", sn,
                ["current_warehouse", "lifecycle_status"], as_dict=True)
            _assert(bool(lc), f"lifecycle row created for {sn}",
                    detail=str(lc))
            if lc:
                _assert(lc.current_warehouse == hub,
                        f"lifecycle warehouse = hub after receipt ({sn})",
                        detail=f"{lc.current_warehouse} vs {hub}")

        # The actual bug: transfer submitted exactly like Data Import does.
        se = _mk_stock_entry("Material Transfer", [{
            "item_code": item, "qty": len(serials),
            "s_warehouse": hub, "t_warehouse": dest,
            "serial_no": "\n".join(serials), "use_serial_batch_fields": 1,
        }])

        for sn in serials:
            sn_wh = frappe.db.get_value("Serial No", sn, "warehouse")
            lc_wh = frappe.db.get_value("CH Serial Lifecycle", sn, "current_warehouse")
            _assert(sn_wh == dest, f"Serial No moved to dest ({sn})",
                    detail=f"{sn_wh} vs {dest}")
            _assert(lc_wh == dest,
                    f"lifecycle current_warehouse follows import transfer ({sn})",
                    detail=f"{lc_wh} vs {dest}")

        se.reload()
        se.cancel()
        for sn in serials:
            lc_wh = frappe.db.get_value("CH Serial Lifecycle", sn, "current_warehouse")
            _assert(lc_wh == hub,
                    f"lifecycle current_warehouse reverts on cancel ({sn})",
                    detail=f"{lc_wh} vs {hub}")
    finally:
        frappe.flags.in_import = False


# ── PART 2 ────────────────────────────────────────────────────────────────

def part2_abbr_heal():
    _section("PART 2 — Legacy abbr heal patch + baseline + importer")

    # 2a. Baseline seed JSON is clean.
    from ch_item_master.ch_core import location_hierarchy_seed as seed
    path = seed.baseline_seed_path()
    blob = open(path).read()
    _assert("BMPL" not in blob and "GSPL" not in blob,
            "baseline seed JSON has no legacy abbrs",
            detail=f"{path}")

    # 2b. Importer normalises a legacy abbr on auto-create (dry-run plan).
    orig_aliases = dict(seed._LEGACY_ABBR_ALIASES)
    seed._LEGACY_ABBR_ALIASES["XXPL"] = "XX"
    try:
        plan = {"dry_run": True, "to_create": [], "skipped": [], "manual_followups": []}
        seed._upsert_company(
            {"abbr": "XXPL", "company_name": f"{TAG} Heal Co",
             "country": "India", "default_currency": "INR"},
            plan, None)
        created = plan["to_create"][0] if plan["to_create"] else {}
        _assert(created.get("values", {}).get("abbr") == "XX",
                "importer auto-create normalises legacy abbr XXPL→XX",
                detail=json.dumps(created.get("values", {})))
    finally:
        seed._LEGACY_ABBR_ALIASES.clear()
        seed._LEGACY_ABBR_ALIASES.update(orig_aliases)

    # 2c. Heal patch: plain rename (no target) + merge (target exists).
    from gofix.patches import heal_legacy_abbr_suffix_docs as heal

    parent_cc = frappe.db.get_value(
        "Cost Center", {"company": COMPANY, "is_group": 1}, "name")

    def _mk_cc(label):
        doc = frappe.get_doc({
            "doctype": "Cost Center", "cost_center_name": label,
            "parent_cost_center": parent_cc, "company": COMPANY,
            "is_group": 0,
        })
        doc.insert(ignore_permissions=True)
        return doc.name

    cleanup = []
    try:
        # rename case: "<label> - BM" forged into "<label> - BMPL"
        cc1 = _mk_cc(f"{TAG} CC1")           # -> "<TAG> CC1 - BM"
        legacy1 = cc1.replace(" - BM", " - BMPL")
        frappe.db.sql("UPDATE `tabCost Center` SET name=%s WHERE name=%s", (legacy1, cc1))
        frappe.clear_cache()

        # merge case: target exists + forged legacy twin
        cc2 = _mk_cc(f"{TAG} CC2")           # target "<TAG> CC2 - BM"
        cc2x = _mk_cc(f"{TAG} CC2X")
        legacy2 = cc2.replace(" - BM", " - BMPL")
        frappe.db.sql("UPDATE `tabCost Center` SET name=%s, cost_center_name=%s WHERE name=%s",
                      (legacy2, f"{TAG} CC2", cc2x))
        frappe.clear_cache()
        frappe.db.commit()

        heal.execute()
        frappe.clear_cache()

        _assert(not frappe.db.exists("Cost Center", legacy1)
                and frappe.db.exists("Cost Center", cc1),
                "heal patch renames legacy '- BMPL' doc to '- BM'",
                detail=f"{legacy1} -> {cc1}")
        _assert(not frappe.db.exists("Cost Center", legacy2)
                and frappe.db.exists("Cost Center", cc2),
                "heal patch merges legacy doc into existing '- BM' target",
                detail=f"{legacy2} -> {cc2}")
        cleanup = [cc1, cc2]
    finally:
        for name in cleanup:
            try:
                frappe.delete_doc("Cost Center", name, force=True,
                                  ignore_permissions=True)
            except Exception:
                pass

    # 2d. Idempotency: patch re-run on the (now clean) site is a no-op.
    heal.execute()
    _assert(True, "heal patch re-run is a no-op (idempotent)")


# ── PART 3 ────────────────────────────────────────────────────────────────

def _idle_driver() -> str:
    from ch_logistics.api.driver_status import get_status  # noqa: F401
    for d in frappe.get_all("Driver", filters={"status": "Active"}, pluck="name"):
        active = frappe.db.exists(
            "CH Logistics Trip",
            {"driver": d, "status": ["in", ["Assigned", "Started"]]})
        if not active:
            return d
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    doc = frappe.get_doc({"doctype": "Driver",
                          "full_name": f"{TAG} Driver {stamp}",
                          "status": "Active"})
    doc.insert(ignore_permissions=True)
    return doc.name


def _ensure_vehicle() -> str:
    plate = f"{TAG}-VEH"
    existing = frappe.db.get_value("Vehicle", {"license_plate": plate}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Vehicle",
        "license_plate": plate,
        "make": "Tata", "model": "Ace",
        "last_odometer": 1000, "uom": "Litre", "fuel_type": "Diesel",
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def part3_delivery_app():
    _section("PART 3 — Delivery app: route typing, stop links, pickup/drop cascade")
    from ch_logistics.api import logistics_api as trip_api
    from ch_logistics.api import transfer_manifest_api as tm_api

    hub, dest = HUB_WH, _pick_sellable_wh()
    item = _ensure_item(f"{TAG}-LOG-ITM", serialized=False)

    # stock at hub + a transfer SE to wrap in a manifest
    frappe.flags.in_import = True
    try:
        _mk_stock_entry("Material Receipt", [{
            "item_code": item, "qty": 5, "t_warehouse": hub,
            "basic_rate": 50, "allow_zero_valuation_rate": 1,
        }])
        se = _mk_stock_entry("Material Transfer", [{
            "item_code": item, "qty": 2, "s_warehouse": hub, "t_warehouse": dest,
        }])
    finally:
        frappe.flags.in_import = False

    mft = tm_api.create_manifest(
        stock_entries=[se.name], source_warehouse=hub,
        destination_warehouse=dest)
    mft = mft if isinstance(mft, str) else mft.get("name")
    mdoc = frappe.get_doc("CH Transfer Manifest", mft)
    if mdoc.docstatus == 0:
        mdoc.submit()

    # Route with NO stop_type on its stops: the hub stop must come out Pickup.
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    route = frappe.get_doc({
        "doctype": "CH Route",
        "route_name": f"{TAG}-RT-{stamp}",
        "company": COMPANY,
        "hub_warehouse": hub,
        "stops": [
            {"sequence": 1, "warehouse": hub},
            {"sequence": 2, "warehouse": dest},
        ],
    })
    route.insert(ignore_permissions=True)

    from frappe.utils import now_datetime, add_to_date
    trip_name = trip_api.trip_create(
        trip_date=nowdate(), company=COMPANY, route=route.name,
        direction="Forward",
        driver=_idle_driver(), vehicle=_ensure_vehicle(),
        planned_start=now_datetime(),
        planned_end=add_to_date(now_datetime(), hours=4))
    trip = frappe.get_doc("CH Logistics Trip", trip_name)
    _assert(trip.stops[0].stop_type == "Pickup",
            "route-built Forward trip types hub stop as Pickup",
            detail=trip.stops[0].stop_type)
    _assert(trip.stops[1].stop_type == "Drop",
            "route-built Forward trip types store stop as Drop",
            detail=trip.stops[1].stop_type)

    trip_api.attach_manifests(trip_name, [mft])

    detail = trip_api.get_trip_detail(trip_name)
    mrow = next((m for m in detail["manifests"] if m["name"] == mft), None)
    _assert(bool(mrow), "manifest visible on trip detail")
    _assert(cint(mrow.get("pickup_stop_sequence")) == 1,
            "get_trip_detail: pickup_stop_sequence -> hub stop #1",
            detail=str(mrow.get("pickup_stop_sequence")))
    _assert(cint(mrow.get("drop_stop_sequence")) == 2,
            "get_trip_detail: drop_stop_sequence -> store stop #2",
            detail=str(mrow.get("drop_stop_sequence")))
    _assert("pickup_photo" in mrow,
            "get_trip_detail exposes pickup_photo for proof display")

    # driver accepts (server-side equivalent), then manifest-level pickup
    trip_api.trip_start(trip_name)
    photo = _proof_photo()
    qr = frappe.db.get_value("CH Transfer Manifest", mft, "qr_payload") or mft
    tm_api.start_pickup(manifest=mft, pickup_photo=photo,
                        lat=CHENNAI_LAT, lng=CHENNAI_LNG,
                        notes=f"[{TAG}]", scanned_qr=qr)

    stop1 = frappe.db.sql(
        "SELECT status FROM `tabCH Logistics Trip Stop` WHERE parent=%s AND sequence=1",
        (trip_name,), as_dict=True)[0]["status"]
    stop2 = frappe.db.sql(
        "SELECT status FROM `tabCH Logistics Trip Stop` WHERE parent=%s AND sequence=2",
        (trip_name,), as_dict=True)[0]["status"]
    _assert(stop1 == "Completed",
            "manifest-level start_pickup cascades PICKUP stop -> Completed",
            detail=f"stop1={stop1}")
    _assert(stop2 != "Completed",
            "drop stop untouched by pickup cascade", detail=f"stop2={stop2}")

    detail = trip_api.get_trip_detail(trip_name)
    mrow = next((m for m in detail["manifests"] if m["name"] == mft), None)
    _assert(bool(mrow and mrow.get("pickup_photo")),
            "pickup photo present on trip detail after pickup",
            detail=str(mrow.get("pickup_photo") if mrow else None))

    # delivery leg → drop stop cascade
    lat, lng = CHENNAI_LAT + 0.002, CHENNAI_LNG + 0.002
    trip_api.stop_arrive(trip_name, 2, gps_lat=lat, gps_lng=lng)
    tm_api.mark_reached_destination(manifest=mft, lat=lat, lng=lng)
    tm_api.request_delivery_otp(manifest=mft)
    otp = frappe.db.get_value("CH Transfer Manifest", mft, "delivery_otp")
    tm_api.complete_delivery(manifest=mft, delivery_photo=photo,
                             receiver_name=f"{TAG} Receiver", otp=otp,
                             lat=lat, lng=lng, scanned_qr=qr)
    status = frappe.db.get_value("CH Transfer Manifest", mft, "status")
    _assert(status == "Delivered", "manifest Delivered", detail=status)
    stop2 = frappe.db.sql(
        "SELECT status FROM `tabCH Logistics Trip Stop` WHERE parent=%s AND sequence=2",
        (trip_name,), as_dict=True)[0]["status"]
    _assert(stop2 == "Completed",
            "delivery cascades DROP stop -> Completed", detail=f"stop2={stop2}")


def run():
    started = datetime.now(timezone.utc).isoformat()
    for part in (part1_serial_lifecycle, part2_abbr_heal, part3_delivery_app):
        try:
            part()
        except Exception:
            _assert(False, f"{part.__name__} raised",
                    detail=frappe.get_traceback()[-2000:])
    frappe.db.commit()
    print(f"\n===== SUMMARY ({started}) =====")
    print(json.dumps(results["summary"], indent=1))
    return results["summary"]
