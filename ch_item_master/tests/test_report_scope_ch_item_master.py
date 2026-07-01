# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
Tier 4 — Report scope injection E2E tests for ch_item_master.

Verifies:
  * The 5 store-dim SQL reports run cleanly for Administrator (bypass)
    and for a scoped user with a populated CH User Scope:
      - supplier_scheme/store_contribution
      - supplier_scheme/scheme_achievement_report
      - supplier_scheme/pending_compliance
      - ch_item_master/ch_pos_override_audit
      - ch_core/stores_by_location
  * ``ch_erp15.report_scope.scope_where_clause`` returns the expected
    fragment for the specific dim keys used
    (Scheme Achievement Ledger.store → warehouse, CH POS Override
    Log.store_warehouse + pos_profile, CH Store.name → store).
  * The other 17 ch_item_master reports are catalog / master data with
    no store dim — global by design (SAP MARA / Oracle
    MTL_SYSTEM_ITEMS_B / D365 EcoResProduct parity) and correctly rely
    on role-based access alone.
"""

from __future__ import annotations

import unittest

import frappe

from ch_erp15.ch_erp15.report_scope import scope_where_clause
from ch_erp15.ch_erp15.scope import clear_scope_cache


_TEST_USER = "tier4-itemmaster-user@ch-tests.local"
_TEST_STORE = "TIER4-IM-STORE-A"


def _ensure_user(user: str) -> None:
    if frappe.db.exists("User", user):
        return
    doc = frappe.new_doc("User")
    doc.email = user
    doc.first_name = "Tier4Im"
    doc.enabled = 1
    doc.new_password = "TestPass123!Tier4"
    doc.send_welcome_email = 0
    doc.append("roles", {"role": "Accounts User"})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


def _get_or_create_warehouse(name: str, company: str) -> str:
    abbr = frappe.db.get_value("Company", company, "abbr")
    full = f"{name} - {abbr}"
    if frappe.db.exists("Warehouse", full):
        return full
    doc = frappe.new_doc("Warehouse")
    doc.warehouse_name = name
    doc.company = company
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _get_or_create_ch_store(name: str, warehouse: str, company: str) -> None:
    if frappe.db.exists("CH Store", name):
        return
    doc = frappe.new_doc("CH Store")
    doc.store_id = name
    doc.store_code = name
    doc.store_name = name
    doc.company = company
    doc.warehouse = warehouse
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)


def _make_scope(user: str, store: str) -> None:
    for row in frappe.get_all("CH User Scope", filters={"user": user}, pluck="name"):
        frappe.delete_doc("CH User Scope", row, ignore_permissions=True, force=True)
    doc = frappe.new_doc("CH User Scope")
    doc.user = user
    doc.scope_role = "Store Executive"
    doc.enabled = 1
    doc.append("stores", {"store": store})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


class TestReportScopeChItemMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_value("Company", {}, "name")
        if not cls.company:
            raise Exception("No Company in this site — cannot run Tier 4 ch_item_master tests.")

        cls.wh_in_scope = _get_or_create_warehouse("Tier4 Im A WH", cls.company)
        _get_or_create_ch_store(_TEST_STORE, cls.wh_in_scope, cls.company)
        _ensure_user(_TEST_USER)
        _make_scope(_TEST_USER, _TEST_STORE)
        clear_scope_cache(_TEST_USER)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user(_TEST_USER)
        clear_scope_cache(_TEST_USER)

    def tearDown(self):
        frappe.set_user("Administrator")

    # ── shared helper contract ──────────────────────────────────────────

    # 1 — scope_where_clause returns fragment for Scheme Achievement Ledger
    def test_01_sal_scope_scoped(self):
        clause = scope_where_clause(warehouse_field="sal.store")
        self.assertIsNotNone(clause)
        self.assertIn("sal.store", clause)

    # 2 — POS Override Log dims (warehouse + pos_profile)
    def test_02_pos_override_scope_scoped(self):
        clause = scope_where_clause(
            warehouse_field="l.store_warehouse",
            pos_profile_field="l.pos_profile",
        )
        self.assertIsNotNone(clause)
        # At minimum the warehouse clause is present.
        self.assertIn("l.store_warehouse", clause)

    # 3 — CH Store directory scope on name
    def test_03_ch_store_scope_scoped(self):
        clause = scope_where_clause(store_field="s.name")
        self.assertIsNotNone(clause)
        self.assertIn("s.name", clause)

    # 4 — Bypass user gets None across every dim pattern
    def test_04_bypass_all_dims(self):
        frappe.set_user("Administrator")
        self.assertIsNone(scope_where_clause(warehouse_field="sal.store"))
        self.assertIsNone(scope_where_clause(store_field="s.name"))
        self.assertIsNone(
            scope_where_clause(
                warehouse_field="l.store_warehouse",
                pos_profile_field="l.pos_profile",
            )
        )

    # ── report end-to-end smoke ─────────────────────────────────────────

    # 5 — Scheme Achievement Ledger reports run for scoped user
    def test_05_sal_reports_scoped(self):
        from ch_item_master.supplier_scheme.report.store_contribution.store_contribution import (
            execute as sc_execute,
        )
        from ch_item_master.supplier_scheme.report.scheme_achievement_report.scheme_achievement_report import (
            execute as sar_execute,
        )
        from ch_item_master.supplier_scheme.report.pending_compliance.pending_compliance import (
            execute as pc_execute,
        )
        for fn in (sc_execute, sar_execute, pc_execute):
            result = fn({})
            self.assertTrue(len(result) >= 2, f"{fn.__module__} should return columns+data")

    # 6 — CH POS Override Audit runs for scoped user
    def test_06_pos_override_audit_scoped(self):
        from ch_item_master.ch_item_master.report.ch_pos_override_audit.ch_pos_override_audit import (
            execute as ov_execute,
        )
        result = ov_execute({})
        self.assertTrue(len(result) >= 2)

    # 7 — Stores by Location runs for scoped user
    def test_07_stores_by_location_scoped(self):
        from ch_item_master.ch_core.report.stores_by_location.stores_by_location import (
            execute as sbl_execute,
        )
        result = sbl_execute({})
        self.assertTrue(len(result) >= 2)

    # 8 — Administrator bypass runs every touched report
    def test_08_administrator_bypass(self):
        frappe.set_user("Administrator")
        from ch_item_master.supplier_scheme.report.store_contribution.store_contribution import (
            execute as sc_execute,
        )
        from ch_item_master.supplier_scheme.report.scheme_achievement_report.scheme_achievement_report import (
            execute as sar_execute,
        )
        from ch_item_master.supplier_scheme.report.pending_compliance.pending_compliance import (
            execute as pc_execute,
        )
        from ch_item_master.ch_item_master.report.ch_pos_override_audit.ch_pos_override_audit import (
            execute as ov_execute,
        )
        from ch_item_master.ch_core.report.stores_by_location.stores_by_location import (
            execute as sbl_execute,
        )
        sc_execute({})
        sar_execute({})
        pc_execute({})
        ov_execute({})
        sbl_execute({})
