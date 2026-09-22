# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Renewal is where a care plan either keeps its customer or loses them.

The two rules under test are the ones that cost money when they are wrong.

**Cover is never backdated.** A renewal taken out before expiry must start the
day after the old term ends — continuous, no gap and no overlap. A renewal
taken out after it has lapsed must start *today*. Starting it at the old end
date would sell someone cover for a period that has already happened, and the
first thing a customer does with backdated cover is claim on the damage that
made them ring up.

**A plan renews once.** Two renewals of one term fork the chain: two live plans
on one device, each believing it is the current one, each recognising revenue.
The guard reads the table rather than a flag, so it holds against a double
click and against two people working the same list.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import add_days, add_months, getdate, nowdate

from ch_item_master.ch_item_master.vas_renewal import renew_plan, renewals_due

SERIAL = "_CHTEST-VAS-0001"


def _plan_master():
    """An active warranty plan with a real duration, and a device it covers.

    The plan doctype checks the device's ``ch_category`` against the plan's
    applicable categories, so the pair has to be chosen together — picking any
    item and any plan gets a device-not-covered rejection, which is the
    doctype behaving correctly.
    """
    # The service item has to be live too. An item in lifecycle status "Draft"
    # is refused by Sales Invoice, so a plan pointing at one cannot be sold and
    # the fixture dies on the sale rather than on anything under test. Without
    # this the suite passes or fails depending on which plan happens to sort
    # first, which changes as data comes and goes.
    candidates = frappe.db.sql(
        """SELECT wp.name, wp.company, wp.duration_months, wp.price,
                  wp.max_claims, wp.service_item
           FROM `tabCH Warranty Plan` wp
           JOIN `tabItem` i ON i.name = wp.service_item
           WHERE wp.status = 'Active' AND wp.duration_months > 0
             AND IFNULL(i.disabled, 0) = 0
             AND (i.ch_lifecycle_status IS NULL OR i.ch_lifecycle_status = 'Active')
           ORDER BY wp.name""",
        as_dict=True,
    )
    for master in candidates:
        cats = frappe.get_all("CH Warranty Plan Category",
                              filters={"parent": master.name}, pluck="category")
        if cats:
            item = frappe.db.get_value(
                "Item",
                {"disabled": 0, "ch_category": ("in", cats), "has_variants": 0,
                 "is_sales_item": 1}, "name")
        else:
            item = frappe.db.get_value(
                "Item",
                {"disabled": 0, "ch_category": ("is", "set"), "has_variants": 0,
                 "is_sales_item": 1}, "name")
        if item:
            master["item_code"] = item
            return master
    return None


def _ensure_serial(item_code):
    """A Serial No for the covered device.

    Created rather than found: no device in any plan-covered category has a
    single Serial No row on this site, so there is nothing to borrow. Rolled
    back with the rest of the fixture.
    """
    if not frappe.db.exists("Serial No", SERIAL):
        sn = frappe.new_doc("Serial No")
        sn.serial_no = SERIAL
        sn.item_code = item_code
        sn.flags.ignore_permissions = True
        sn.flags.ignore_mandatory = True
        sn.insert(ignore_permissions=True)
    return SERIAL


def _sales_invoice(master, customer, company, device_item=None, serial_no=None):
    """The sale a plan hangs off.

    A Sales **Invoice**, not a Sales Order, and that is forced rather than
    chosen: the plan doctype checks the covered serial against the source
    document's rows, and in v16 ``Sales Order Item`` has no ``serial_no``
    field at all — only ``Sales Invoice Item`` does. Every device these plans
    cover (phones, laptops, tablets, televisions) is serial-tracked, so a
    Sales Order source can never satisfy that check.

    ``update_stock`` stays off so the invoice is a commercial record only and
    no serial has to exist in a bin for the test to run.
    """
    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.company = company
    si.posting_date = nowdate()
    si.due_date = nowdate()
    si.update_stock = 0
    si.append("items", {"item_code": master.service_item, "qty": 1, "rate": 0})
    if device_item:
        _ensure_serial(device_item)
        si.append("items", {
            "item_code": device_item, "qty": 1, "rate": 0, "serial_no": serial_no or "",
        })
    si.flags.ignore_permissions = True
    si.flags.ignore_mandatory = True
    si.insert(ignore_permissions=True)
    si.submit()
    # The rollback in tearDown restores the naming series but not the document
    # cache, so the next test mints this same invoice name and frappe.get_doc
    # hands back the previous test's copy — still docstatus 0. The plan then
    # refuses to activate against an invoice that is, in the database,
    # submitted. Clearing it here keeps each test honest.
    frappe.clear_document_cache("Sales Invoice", si.name)

    # Fixture repair, and worth explaining because it looks like a cover-up.
    #
    # Under the test runner only, the *second* Sales Invoice submitted inside
    # one test leaves docstatus 0 in the database while reporting 1 in memory.
    # The first one persists correctly. The same sequence run through
    # `bench console` — with frappe.flags.in_test set — persists both, so this
    # is the harness's transaction handling, not the submit path, and not
    # something a real request hits. Every test here needs two invoices: one
    # for the original sale and one for the renewal.
    #
    # The alternative was to reuse the original invoice as the renewal's
    # source, which would have been quieter and wrong: that invoice carries a
    # device row, so the renewal would never take the device-less path this
    # work added, and the code under test would go unexercised.
    if frappe.db.get_value("Sales Invoice", si.name, "docstatus") != 1:
        frappe.db.set_value("Sales Invoice", si.name, "docstatus", 1,
                            update_modified=False)
        frappe.clear_document_cache("Sales Invoice", si.name)
    return si.name


def _sold_plan(master, *, end_date, start_date=None, status="Active", claims_used=0):
    """A submitted Active VAS Plan ending on a given date."""
    company = master.company or frappe.db.get_value("Company", {}, "name")
    customer = frappe.db.get_value("Customer", {}, "name")
    item = master.item_code
    doc = frappe.new_doc("Active VAS Plans")
    doc.update({
        "company": company,
        "warranty_plan": master.name,
        "customer": customer,
        "item_code": item,
        "start_date": start_date or add_months(end_date, -12),
        "end_date": end_date,
        "duration_months": master.duration_months,
        "max_claims": master.max_claims or 2,
        "claims_used": claims_used,
        "serial_no": SERIAL,
        "plan_price": master.price or 0,
        "sales_invoice": _sales_invoice(master, customer, company,
                                        device_item=item, serial_no=SERIAL),
    })
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)
    doc.submit()
    # The doctype recomputes end_date from the sale date and the plan's
    # duration, so the value passed to insert() never survives. These tests
    # are about which plans are near expiry, so the date is set afterwards
    # rather than fought for during validation.
    doc.db_set("end_date", end_date)
    if status != "Active":
        doc.db_set("status", status)
    doc.reload()
    frappe.clear_document_cache("Active VAS Plans", doc.name)
    return doc


def _renewal_source(plan):
    """The sale that pays for the next term — the plan line only.

    Deliberately carries no device row: at renewal the customer is not buying
    the handset again, which is exactly the case the doctype now resolves
    through ``renewed_from``.
    """
    master = frappe.db.get_value(
        "CH Warranty Plan", plan.warranty_plan, ["name", "service_item"], as_dict=True)
    return _sales_invoice(master, plan.customer, plan.company)


class TestRenewalDating(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.master = _plan_master()
        if not cls.master:
            raise unittest.SkipTest("no active CH Warranty Plan with a duration on this site")

    def tearDown(self):
        frappe.db.rollback()

    def test_renewing_early_is_continuous(self):
        """No gap and no overlap: the new term starts the day after the old ends."""
        old_end = add_days(nowdate(), 10)
        plan = _sold_plan(self.master, end_date=old_end)
        out = renew_plan(plan.name, sales_invoice=_renewal_source(plan))

        self.assertEqual(getdate(out["start_date"]), getdate(add_days(old_end, 1)),
                         "a renewal taken out before expiry must continue the old term exactly")
        self.assertTrue(out["continuous"])

    def test_renewing_after_it_lapsed_starts_today_not_at_the_old_end(self):
        """The rule that stops cover being sold for days that already happened."""
        old_end = add_days(nowdate(), -20)
        plan = _sold_plan(self.master, end_date=old_end)
        out = renew_plan(plan.name, sales_invoice=_renewal_source(plan))

        self.assertEqual(getdate(out["start_date"]), getdate(nowdate()),
                         "a lapsed plan was renewed with 20 days of backdated cover")
        self.assertFalse(out["continuous"])

    def test_the_term_length_comes_from_the_plan(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5))
        out = renew_plan(plan.name, sales_invoice=_renewal_source(plan))
        expected = add_months(getdate(out["start_date"]), self.master.duration_months)
        self.assertEqual(getdate(out["end_date"]), getdate(expected))

    def test_a_renewal_cannot_be_backdated_by_hand(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5))
        with self.assertRaises(frappe.ValidationError):
            renew_plan(plan.name, start_date=add_days(nowdate(), -1),
                       sales_invoice=_renewal_source(plan))


class TestRenewalIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.master = _plan_master()
        if not cls.master:
            raise unittest.SkipTest("no active CH Warranty Plan with a duration on this site")

    def tearDown(self):
        frappe.db.rollback()

    def test_a_plan_renews_only_once(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5))
        renew_plan(plan.name, sales_invoice=_renewal_source(plan))
        with self.assertRaises(frappe.ValidationError):
            renew_plan(plan.name, sales_invoice=_renewal_source(plan))

    def test_the_chain_is_recorded_on_the_renewal(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5))
        out = renew_plan(plan.name, sales_invoice=_renewal_source(plan))
        self.assertEqual(
            frappe.db.get_value("Active VAS Plans", out["renewal"], "renewed_from"),
            plan.name, "the renewal must say which plan it continues")

    def test_a_new_term_buys_a_new_claim_allowance(self):
        """Carrying claims forward would sell a year of cover already used up."""
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5), claims_used=2)
        out = renew_plan(plan.name, sales_invoice=_renewal_source(plan))
        self.assertEqual(
            int(frappe.db.get_value("Active VAS Plans", out["renewal"], "claims_used")), 0)

    def test_a_void_plan_is_not_renewable(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5), status="Void")
        with self.assertRaises(frappe.ValidationError):
            renew_plan(plan.name, sales_invoice=_renewal_source(plan))

    def test_a_cancelled_plan_is_not_renewable(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5), status="Cancelled")
        with self.assertRaises(frappe.ValidationError):
            renew_plan(plan.name, sales_invoice=_renewal_source(plan))

    def test_a_withdrawn_product_cannot_be_renewed_onto_silently(self):
        """If the plan has been retired, someone must choose the replacement.

        Substituting one automatically would renew the customer onto terms
        nobody showed them.
        """
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5))
        frappe.db.set_value("CH Warranty Plan", self.master.name, "status", "Inactive")
        frappe.clear_document_cache("CH Warranty Plan", self.master.name)
        with self.assertRaises(frappe.ValidationError):
            renew_plan(plan.name, sales_invoice=_renewal_source(plan))

    def test_the_renewal_is_written_to_the_ledger(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 5))
        out = renew_plan(plan.name, sales_invoice=_renewal_source(plan))
        self.assertTrue(
            frappe.db.exists("CH VAS Ledger",
                             {"sold_plan": out["renewal"], "event_type": "Plan Renewed"}),
            "a renewal with no ledger entry is invisible to every audit")


class TestRenewalsDue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.master = _plan_master()
        if not cls.master:
            raise unittest.SkipTest("no active CH Warranty Plan with a duration on this site")

    def tearDown(self):
        frappe.db.rollback()

    def _names(self, **kw):
        return [r["name"] for r in renewals_due(**kw)]

    def test_a_plan_expiring_inside_the_window_is_listed(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 7))
        self.assertIn(plan.name, self._names(within_days=30))

    def test_a_plan_expiring_beyond_the_window_is_not(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 120))
        self.assertNotIn(plan.name, self._names(within_days=30))

    def test_a_recently_lapsed_plan_is_still_offered(self):
        """The fortnight after expiry is when a renewal is most winnable."""
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), -5))
        self.assertIn(plan.name, self._names(within_days=30, grace_days=30))

    def test_a_long_lapsed_plan_falls_off_the_list(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), -400))
        self.assertNotIn(plan.name, self._names(within_days=30, grace_days=30))

    def test_renewing_takes_it_off_the_list(self):
        """The list must be a worklist, not a log of everything that ever expired."""
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 7))
        self.assertIn(plan.name, self._names(within_days=30))
        renew_plan(plan.name, sales_invoice=_renewal_source(plan))
        self.assertNotIn(plan.name, self._names(within_days=30),
                         "a renewed plan stayed on the due list")

    def test_a_void_plan_is_not_a_renewal_to_chase(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 7), status="Void")
        self.assertNotIn(plan.name, self._names(within_days=30))

    def test_the_days_to_expiry_is_reported(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 7))
        row = next(r for r in renewals_due(within_days=30) if r["name"] == plan.name)
        self.assertEqual(row["days_to_expiry"], 7)
        self.assertFalse(row["lapsed"])


class TestVasHubExpiringList(unittest.TestCase):
    """The hub list that has always asked staff to chase renewals.

    The VAS Hub shows what is about to lapse and raises an insight reading
    "Opportunity for renewal outreach before coverage lapses." Until now there
    was no renewal to make, so the advice ended nowhere. Now that it does, a
    plan that has been renewed has to leave the list — otherwise the next
    person works down it and rings a customer who is already covered.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.master = _plan_master()
        if not cls.master:
            raise unittest.SkipTest("no active CH Warranty Plan with a duration on this site")

    def tearDown(self):
        frappe.db.rollback()

    def _expiring(self):
        from ch_item_master.ch_item_master.page.vas_hub.vas_hub_api import get_vas_hub_data

        data = get_vas_hub_data() or {}
        return [r["name"] for r in (data.get("expiring_soon") or [])]

    def test_an_expiring_plan_is_on_the_hub_list(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 10))
        self.assertIn(plan.name, self._expiring())

    def test_renewing_takes_it_off_the_hub_list(self):
        plan = _sold_plan(self.master, end_date=add_days(nowdate(), 10))
        self.assertIn(plan.name, self._expiring())
        renew_plan(plan.name, sales_invoice=_renewal_source(plan))
        self.assertNotIn(
            plan.name, self._expiring(),
            "a renewed plan stayed on the hub's expiring list — it will be chased twice")
