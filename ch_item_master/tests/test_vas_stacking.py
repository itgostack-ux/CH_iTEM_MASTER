# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""When a plan's cover actually starts, across the plans this market sells.

The Indian phone market sells four shapes of protection, and they do not start
at the same moment:

  Extended Warranty      manufacturer defects only. Runs *after* the base
                         warranty, because until then the manufacturer is
                         already on the hook for exactly the same failures.
  Damage / ADLD          accidental and liquid damage. Starts on the day of
                         sale, because the base warranty never covered damage
                         and there is nothing to wait behind.
  Screen protection      one component, usually one claim. Starts immediately
                         for the same reason.
  Post-repair warranty   cover on a repair GoFix performed.

``starts_after_base_warranty`` is the flag that separates the first from the
rest, and on this estate all 23 active plans currently have it off.

The same logic extends to a plan sold over the repair counter, and it is not
unconditional. A screen replaced last week carries its own cover, so a screen
plan sold today would be charging for a period already covered and should wait.
An extended warranty would not — it never covered that damage, so it starts
now. Whether a repair delays a plan depends on whether the plan covers what was
repaired, which the plan declares through ``coverage_rules`` (per issue
category) or, for "Full Device", by definition.

Where the plan does not say, cover starts immediately. Withholding a term
someone has paid for needs a reason, and "we could not tell" is not one.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import add_days, getdate, nowdate

from ch_item_master.ch_item_master.warranty_api import issue_warranty_plan
from ch_item_master.tests.test_vas_renewal import (
    SERIAL,
    _ensure_serial,
    _plan_master,
    _sales_invoice,
)


def _fit_part(sr, days):
    """Fit a spare carrying its own supplier warranty onto a delivered repair."""
    # Ordered and filtered rather than "any stock item": without this the row
    # MySQL returns varies between runs, and a template item makes the spare
    # line throw. That is the shape of a test that passes three times and
    # errors on the fourth.
    spare = frappe.db.sql(
        """SELECT name FROM `tabItem`
           WHERE IFNULL(disabled, 0) = 0 AND is_stock_item = 1
             AND IFNULL(has_variants, 0) = 0
           ORDER BY name LIMIT 1""",
        pluck=True,
    )
    spare = spare[0] if spare else None
    if not spare:
        return None
    frappe.db.set_value("Item", spare, "gofix_part_warranty_days", days, update_modified=False)
    frappe.clear_document_cache("Item", spare)
    sr.append("spare_lines", {"spare_item": spare, "qty": 1, "status": "Consumed"})
    sr.flags.ignore_permissions = True
    sr.save(ignore_permissions=True)
    frappe.clear_document_cache("Service Request", sr.name)
    return spare


def _delivered_repair(serial_no, item_code, company, warranty_expiry):
    """A handed-over repair whose workmanship cover runs to a given date.

    The expiry is written directly rather than driven through completion: what
    is under test is what the plan does with that date, not how the repair
    desk arrives at it (which `test_repair_warranty_days` covers).
    """
    from gofix.tests.test_service_maturity import _minimal_service_request

    sr = _minimal_service_request()
    if not sr:
        return None
    sr.serial_no = serial_no
    sr.device_item = item_code
    sr.product_condition_desc = "Stacking fixture"
    sr.backup_info = "N/A"
    if not sr.state_code:
        sr.state_name, sr.state_code = "Tamil Nadu", "33"
    sr.flags.ignore_permissions = True
    sr.save(ignore_permissions=True)
    sr.submit()
    sr.db_set({
        "decision": "Delivered",
        "actual_completion_date": nowdate(),
        "repair_warranty_expiry": warranty_expiry,
        "repair_warranty_days": (getdate(warranty_expiry) - getdate(nowdate())).days,
    }, update_modified=False)
    frappe.clear_document_cache("Service Request", sr.name)
    return sr


class TestVasStacksOnRepairCover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.master = _plan_master()
        if not cls.master:
            raise unittest.SkipTest("no active CH Warranty Plan with a duration on this site")

    def tearDown(self):
        frappe.db.rollback()

    def _issue(self, stacking):
        frappe.db.set_value("CH Warranty Plan", self.master.name,
                            "starts_after_base_warranty", 1 if stacking else 0)
        frappe.clear_document_cache("CH Warranty Plan", self.master.name)
        customer = frappe.db.get_value("Customer", {}, "name", order_by="name")
        item = self.master.item_code
        _ensure_serial(item)
        si = _sales_invoice(self.master, customer, self.master.company,
                            device_item=item, serial_no=SERIAL)
        return issue_warranty_plan(
            warranty_plan=self.master.name, customer=customer, item_code=item,
            serial_no=SERIAL, company=self.master.company, sales_invoice=si)

    def test_it_starts_the_day_after_the_repair_cover_ends(self):
        """The case the repair counter actually sells into."""
        expiry = add_days(nowdate(), 90)
        sr = _delivered_repair(SERIAL, self.master.item_code, self.master.company, expiry)
        if not sr:
            self.skipTest("could not build a delivered repair on this site")
        out = self._issue(stacking=True)
        self.assertEqual(
            getdate(out["end_date"]), getdate(frappe.db.get_value(
                "Active VAS Plans", out["active_plan"], "end_date")))
        start = getdate(frappe.db.get_value("Active VAS Plans", out["active_plan"], "start_date"))
        self.assertEqual(
            start, getdate(add_days(expiry, 1)),
            "the plan overlapped the repair warranty the customer already had")

    def test_without_stacking_it_starts_at_the_sale(self):
        """The retail case is unchanged — this must not move plans that stack on nothing."""
        expiry = add_days(nowdate(), 90)
        sr = _delivered_repair(SERIAL, self.master.item_code, self.master.company, expiry)
        if not sr:
            self.skipTest("could not build a delivered repair on this site")
        out = self._issue(stacking=False)
        start = getdate(frappe.db.get_value("Active VAS Plans", out["active_plan"], "start_date"))
        self.assertEqual(start, getdate(nowdate()))

    def test_an_expired_repair_cover_does_not_delay_the_plan(self):
        """Cover that has already run out is not cover — it must not push the start."""
        sr = _delivered_repair(SERIAL, self.master.item_code, self.master.company,
                               add_days(nowdate(), -10))
        if not sr:
            self.skipTest("could not build a delivered repair on this site")
        out = self._issue(stacking=True)
        start = getdate(frappe.db.get_value("Active VAS Plans", out["active_plan"], "start_date"))
        self.assertEqual(start, getdate(nowdate()))

    def test_with_no_repair_history_it_behaves_as_before(self):
        out = self._issue(stacking=True)
        start = getdate(frappe.db.get_value("Active VAS Plans", out["active_plan"], "start_date"))
        self.assertGreaterEqual(start, getdate(nowdate()))


class TestMarketPlanShapes(unittest.TestCase):
    """The four shapes this market sells, each starting when it should.

    These drive ``_plan_would_duplicate`` directly rather than issuing a plan
    per case: the question is a policy decision about two dates, and putting a
    sale, an invoice and a device behind each one would test the fixture
    harder than the rule.
    """

    def tearDown(self):
        frappe.db.rollback()

    @staticmethod
    def _cover(*issues):
        return {"expires_on": add_days(nowdate(), 60), "issue_categories": list(issues)}

    # ── Extended warranty: defects only, never covered the damage ───────

    def test_extended_warranty_does_not_wait_behind_a_screen_repair(self):
        """It never covered that screen, so there is nothing to wait for.

        This is the case the customer loses money on if we get it wrong: a
        defects-only plan delayed by 90 days behind a repair it would never
        have paid for.
        """
        from ch_item_master.ch_item_master.warranty_api import _plan_would_duplicate

        plan = frappe._dict({
            "coverage_scope": "Full Device",
            "coverage_rules": [
                frappe._dict({"issue_type": "Screen & Display", "covered": 0}),
                frappe._dict({"issue_type": "Battery & Charging", "covered": 1}),
            ],
        })
        self.assertFalse(_plan_would_duplicate(plan, self._cover("Screen & Display")))

    def test_it_does_wait_behind_a_repair_it_would_have_covered(self):
        from ch_item_master.ch_item_master.warranty_api import _plan_would_duplicate

        plan = frappe._dict({
            "coverage_scope": "Full Device",
            "coverage_rules": [
                frappe._dict({"issue_type": "Battery & Charging", "covered": 1}),
            ],
        })
        self.assertTrue(_plan_would_duplicate(plan, self._cover("Battery & Charging")))

    # ── Full-device damage cover overlaps everything ────────────────────

    def test_full_device_cover_overlaps_any_repair(self):
        """"Full Device" needs no mapping — it covers the lot by definition."""
        from ch_item_master.ch_item_master.warranty_api import _plan_would_duplicate

        plan = frappe._dict({"coverage_scope": "Full Device", "coverage_rules": []})
        self.assertTrue(_plan_would_duplicate(plan, self._cover("Screen & Display")))
        self.assertTrue(_plan_would_duplicate(plan, self._cover("Audio")))

    # ── Narrow scope with nothing declared: start now ───────────────────

    def test_a_narrow_plan_that_declares_nothing_starts_immediately(self):
        """"Screen Only" against "Screen & Display" is a guess, so we don't.

        Guessing wrong in this direction withholds a month the customer paid
        for. The fix is to configure the plan's coverage rules, not to infer
        them from the wording of a scope.
        """
        from ch_item_master.ch_item_master.warranty_api import _plan_would_duplicate

        plan = frappe._dict({"coverage_scope": "Screen Only", "coverage_rules": []})
        self.assertFalse(_plan_would_duplicate(plan, self._cover("Screen & Display")))

    def test_a_narrow_plan_that_does_declare_is_honoured(self):
        from ch_item_master.ch_item_master.warranty_api import _plan_would_duplicate

        plan = frappe._dict({
            "coverage_scope": "Screen Only",
            "coverage_rules": [frappe._dict({"issue_type": "Screen & Display", "covered": 1})],
        })
        self.assertTrue(_plan_would_duplicate(plan, self._cover("Screen & Display")))

    # ── A repair with no recorded category tells us nothing ─────────────

    def test_a_repair_with_no_issue_category_does_not_delay_a_ruled_plan(self):
        from ch_item_master.ch_item_master.warranty_api import _plan_would_duplicate

        plan = frappe._dict({
            "coverage_scope": "Screen Only",
            "coverage_rules": [frappe._dict({"issue_type": "Screen & Display", "covered": 1})],
        })
        self.assertFalse(_plan_would_duplicate(plan, self._cover()))


class TestPartWarrantyDelaysThePlan(unittest.TestCase):
    """"VAS starts after the part warranty is over."

    The part's term and the workmanship term are separate clocks — the part is
    recoverable from the supplier, the labour is ours — and a plan sold over
    the counter has to clear whichever runs longest. Almost no spare carries a
    term today, so this configures one rather than waiting for the catalogue.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.master = _plan_master()
        if not cls.master:
            raise unittest.SkipTest("no active CH Warranty Plan with a duration on this site")

    def tearDown(self):
        frappe.db.rollback()

    def _issue_stacking(self):
        frappe.db.set_value("CH Warranty Plan", self.master.name,
                            "starts_after_base_warranty", 1)
        frappe.clear_document_cache("CH Warranty Plan", self.master.name)
        customer = frappe.db.get_value("Customer", {}, "name", order_by="name")
        _ensure_serial(self.master.item_code)
        si = _sales_invoice(self.master, customer, self.master.company,
                            device_item=self.master.item_code, serial_no=SERIAL)
        out = issue_warranty_plan(
            warranty_plan=self.master.name, customer=customer,
            item_code=self.master.item_code, serial_no=SERIAL,
            company=self.master.company, sales_invoice=si)
        return getdate(frappe.db.get_value("Active VAS Plans", out["active_plan"], "start_date"))

    def test_the_plan_waits_for_the_part_not_just_the_labour(self):
        """A 365-day part outlives a 30-day labour term — the plan clears both."""
        sr = _delivered_repair(SERIAL, self.master.item_code, self.master.company,
                               add_days(nowdate(), 30))
        if not sr:
            self.skipTest("could not build a delivered repair on this site")
        if not _fit_part(sr, 365):
            self.skipTest("no stock item to stand in for a spare")
        start = self._issue_stacking()
        self.assertEqual(
            start, getdate(add_days(nowdate(), 366)),
            "the plan started before the fitted part's own warranty had run out")

    def test_the_latest_of_several_covers_wins(self):
        """Two repairs on one device: cover starts after the longer one."""
        first = _delivered_repair(SERIAL, self.master.item_code, self.master.company,
                                  add_days(nowdate(), 30))
        second = _delivered_repair(SERIAL, self.master.item_code, self.master.company,
                                   add_days(nowdate(), 120))
        if not (first and second):
            self.skipTest("could not build delivered repairs on this site")
        self.assertEqual(self._issue_stacking(), getdate(add_days(nowdate(), 121)))
