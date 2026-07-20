"""
TC_020 regression guard.

Run:
  bench --site erpnext.local execute ch_item_master.ch_item_master.tests.test_tc020_ready_reckoner_inline_edit_guard.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


PASS = 0
FAIL = 0
RESULTS: list[tuple[str, str, str]] = []


def _ok(tid: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    RESULTS.append(("PASS", tid, detail))
    print(f"  PASS {tid}: {detail}" if detail else f"  PASS {tid}")


def _fail(tid: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    RESULTS.append(("FAIL", tid, detail))
    print(f"  FAIL {tid}: {detail}" if detail else f"  FAIL {tid}")


def _read(rel_path: str) -> str:
    base = Path(frappe.get_app_path("ch_item_master"))
    return (base / rel_path).read_text(encoding="utf-8")


def tc020_ready_reckoner_inline_edit() -> None:
    tid = "TC_020"
    page_src = _read("ch_item_master/page/ch_ready_reckoner/ch_ready_reckoner.js")
    api_src = _read("ch_item_master/ready_reckoner_api.py")

    required_markers = {
        "inline_editor_fn": "function _activate_inline_price_editor($cell, state, $wrap) {",
        # MRP is no longer part of this binding: the Ready Reckoner MRP column
        # writes Item.ch_item_mrp directly via update_item_mrp (item master
        # data, deliberately outside maker-checker), so only MOP and Selling
        # Price open the inline batch editor.
        "inline_click_binding": "$t.find('.price-cell[data-field=\"mop\"], .price-cell[data-field=\"selling_price\"]').on('click'",
        "inline_prompt_reason": "Reason for Change",
        "inline_batch_method": "create_price_change_batch",
        "no_add_price_button": "chpb-add-price-btn",
    }

    missing = []
    if required_markers["inline_editor_fn"] not in page_src:
        missing.append("inline_editor_fn")
    if required_markers["inline_click_binding"] not in page_src:
        missing.append("inline_click_binding")
    if required_markers["inline_prompt_reason"] not in page_src:
        missing.append("inline_prompt_reason")
    if required_markers["inline_batch_method"] not in page_src + "\n" + api_src:
        missing.append("inline_batch_method")

    if required_markers["no_add_price_button"] in page_src:
        missing.append("still_has_add_price_popup_button")

    direct_save_marker = "doc.flags.from_ready_reckoner = True"
    if direct_save_marker in api_src:
        missing.append("direct_ready_reckoner_save_bypass")

    # Ensure the old click-to-dialog path for populated selling cells is removed
    legacy_marker = "$t.find('.price-cell[data-price-name]').on('click'"
    if legacy_marker in page_src:
        missing.append("legacy_dialog_click_handler")

    if missing:
        _fail(tid, f"Missing/invalid inline-edit markers: {', '.join(missing)}")
        return

    _ok(tid, "Ready Reckoner selling cells use inline edit + approval batch flow without popup trigger")


def run() -> dict:
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []

    print("\n=== TC_020 Ready Reckoner Inline Edit Guard ===\n")
    tc020_ready_reckoner_inline_edit()

    print(f"\n  Summary: {PASS} pass / {FAIL} fail")
    if FAIL:
        raise AssertionError(f"{FAIL} guard(s) failed")
    return {"pass": PASS, "fail": FAIL}
