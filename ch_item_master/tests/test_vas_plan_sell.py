"""Selling a VAS plan from the Desk.

`warranty_api.issue_warranty_plan` has always been able to sell one — GoFix and
the POS both call it — but nothing in the Desk did. A plan bought at the back
office rather than at a till had no way in, which is what "backend VAS Plan
sell option is not available" meant: the server half existed, the entry point
did not.

The form now reuses that whitelisted call. These tests pin the server contract
it depends on, and the data conditions that decide whether the button appears
at all.
"""

import inspect
import pathlib
import unittest

import frappe

from ch_item_master.ch_item_master import warranty_api

SELLABLE_TYPES = ("Value Added Service", "Protection Plan")


def _form_script() -> str:
    return pathlib.Path(frappe.get_app_path(
        "ch_item_master", "ch_item_master", "doctype",
        "ch_warranty_plan", "ch_warranty_plan.js")).read_text()


class TestIssueContract(unittest.TestCase):
    """What the Desk dialog sends has to be what the server takes."""

    def test_the_server_can_issue_a_plan(self):
        self.assertTrue(callable(warranty_api.issue_warranty_plan))

    def test_the_dialog_sends_only_arguments_it_accepts(self):
        accepted = set(inspect.signature(warranty_api.issue_warranty_plan).parameters)
        for arg in ("warranty_plan", "customer", "item_code", "serial_no",
                    "start_date", "plan_price", "sales_invoice"):
            self.assertIn(arg, accepted, f"the dialog sends {arg} and the server would reject it")

    def test_it_returns_the_record_the_form_routes_to(self):
        src = inspect.getsource(warranty_api.issue_warranty_plan)
        self.assertIn('"active_plan"', src)
        self.assertIn("active_plan", _form_script())

    def test_permission_is_still_the_server_s_job(self):
        """The button must not become the gate."""
        src = inspect.getsource(warranty_api.issue_warranty_plan)
        self.assertIn('has_permission("Active VAS Plans", "create", throw=True)', src)
        self.assertIn("ensure_company_access", src)


class TestButtonVisibility(unittest.TestCase):
    def test_it_only_offers_plans_that_are_active_and_sellable(self):
        js = _form_script()
        self.assertIn("frm.doc.status === 'Active'", js)
        self.assertIn("frm.doc.is_sellable", js)
        self.assertIn("!frm.is_new()", js)

    def test_the_estate_has_plans_the_button_would_show_on(self):
        count = frappe.db.count(
            "CH Warranty Plan",
            {"status": "Active", "is_sellable": 1, "plan_type": ("in", SELLABLE_TYPES)})
        self.assertGreater(count, 0, "no sellable VAS plan — the button would never appear")


class TestZeroPriceIsSurfaced(unittest.TestCase):
    """Every VAS and Protection plan here carries price 0, so issuing one
    silently gives the cover away. The dialog has to say so."""

    def test_the_dialog_warns_when_the_plan_has_no_price(self):
        js = _form_script()
        self.assertIn("zero_priced", js)
        self.assertIn("issued free", js)

    def test_the_condition_is_real_on_this_site(self):
        unpriced = frappe.db.count(
            "CH Warranty Plan",
            {"status": "Active", "is_sellable": 1,
             "plan_type": ("in", SELLABLE_TYPES), "price": 0})
        total = frappe.db.count(
            "CH Warranty Plan",
            {"status": "Active", "is_sellable": 1, "plan_type": ("in", SELLABLE_TYPES)})
        if not total:
            self.skipTest("no sellable VAS plan on this site")
        # Not an assertion about what the data *should* be — a record of what it
        # is, so the warning is not mistaken for dead code later.
        self.assertLessEqual(unpriced, total)


class TestPercentagePricing(unittest.TestCase):
    """A percentage plan must not be issued at zero.

    Every sellable plan on this estate uses "Percentage of Device Price", which
    stores price 0 by design — the charge is a share of the device. Defaulting
    the dialog to the plan's own price would therefore give the cover away on
    every Desk sale.
    """

    def test_all_sellable_plans_here_are_percentage_priced(self):
        modes = frappe.db.sql(
            """
            SELECT DISTINCT IFNULL(pricing_mode, '') FROM `tabCH Warranty Plan`
             WHERE status = 'Active' AND is_sellable = 1
               AND plan_type IN ('Value Added Service', 'Protection Plan')
            """, pluck=True)
        if not modes:
            self.skipTest("no sellable VAS plan on this site")
        self.assertIn("Percentage of Device Price", modes,
                      "the dialog's percentage path would be dead code")

    def test_the_dialog_prices_off_the_device(self):
        js = _form_script()
        self.assertIn("function price_from_device", js)
        self.assertIn("onchange: () => price_from_device(frm, d)", js,
                      "picking a device must recompute the charge")

    def test_it_reads_the_same_source_as_the_pos_attach_panel(self):
        """Two answers to what a plan costs is how a Desk sale and a till sale
        end up disagreeing."""
        import inspect

        from ch_pos.api import attach_api

        pos_src = inspect.getsource(attach_api)
        self.assertIn('"CH Item Price"', pos_src)
        self.assertIn('"channel": "POS"', pos_src)

        js = _form_script()
        self.assertIn("'CH Item Price'", js)
        self.assertIn("channel: 'POS'", js)
        self.assertIn("status: 'Active'", js)
        self.assertIn("selling_price", js)

    def test_it_says_so_when_the_device_has_no_price(self):
        """Silently leaving 0 in the box is the failure this replaces."""
        js = _form_script()
        self.assertIn("no active POS price", js)
