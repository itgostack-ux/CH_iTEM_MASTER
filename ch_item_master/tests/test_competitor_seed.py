"""The competitor-price configuration must survive a database restore.

It lived only in the database, so the Sep-18 production restore erased the two
working scrapers and every item link, and the Price Planner went blank. These
pin the seed: it recreates what is missing, never overwrites what an operator
set, and never switches scraping on by itself.
"""

import re
import unittest

import frappe

from ch_item_master.ch_item_master.competitor_pricing import seed

LINK_SPECS = [
    {"competitor": "Cashkr", "item_code": None, "url": "https://www.cashkr.com/x",
     "match_status": "Auto Matched", "matched_by": "Search"},
]


class SeedHarness(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sources = seed._load("sources.json")
        # Any enabled Mobiles item will do — the link only needs a real target.
        self.item = frappe.get_all(
            "Item", filters={"disabled": 0, "item_group": "Mobiles"}, pluck="name", limit=1)

    def tearDown(self):
        frappe.db.rollback()

    def link_spec(self, item_code=None, competitor="Cashkr"):
        return {**LINK_SPECS[0], "competitor": competitor, "item_code": item_code or self.item[0]}


class TestTheSeedFiles(SeedHarness):
    def test_five_sources_two_of_them_live(self):
        live = [s["competitor_name"] for s in self.sources if not s.get("disabled")]
        self.assertEqual(sorted(live), ["Cashify", "Cashkr"])
        self.assertEqual(len(self.sources), 5)

    def test_live_regexes_compile(self):
        self.assertEqual(seed.live_regexes_compile(), [])

    def test_the_regexes_still_read_the_documented_page_shapes(self):
        by = {s["competitor_name"]: s for s in self.sources}
        # Cashkr: price only in the ESCAPED hydration payload.
        self.assertTrue(re.search(by["Cashkr"]["price_regex"],
                                  r'\"deviceType\":\"Mobile\",\"a\":1,\"price\":25600'))
        # Cashify: anchored on the visible "Get Upto" text, not build-hashed classes.
        self.assertTrue(re.search(by["Cashify"]["price_regex"], "Get Upto <b>₹ 34,030</b>"))

    def test_links_only_name_the_seeded_sources(self):
        names = {s["competitor_name"] for s in self.sources}
        self.assertTrue({l["competitor"] for l in seed._load("item_links.json")} <= names)


class TestSources(SeedHarness):
    def test_missing_sources_are_created_once(self):
        first = seed.seed_sources()
        second = seed.seed_sources()
        self.assertEqual(sorted(first["created"] + [n for n in frappe.get_all(
            "CH Competitor Source", pluck="name") if n not in first["created"]]),
            sorted(s["competitor_name"] for s in self.sources))
        self.assertEqual(second, {"created": [], "filled": []},
                         "a second run created or refilled something")

    def test_an_operator_edit_survives_a_reseed(self):
        """A regex fixed on the site must not be reverted by the next deploy."""
        seed.seed_sources()
        frappe.db.set_value("CH Competitor Source", "Cashkr", "price_regex", "operator-fixed")
        seed.seed_sources()
        self.assertEqual(
            frappe.db.get_value("CH Competitor Source", "Cashkr", "price_regex"), "operator-fixed")

    def test_a_blank_field_is_filled_in(self):
        seed.seed_sources()
        frappe.db.set_value("CH Competitor Source", "Cashify", "price_regex", "")
        result = seed.seed_sources()
        self.assertIn("Cashify", result["filled"])
        self.assertTrue(frappe.db.get_value("CH Competitor Source", "Cashify", "price_regex"))

    def test_a_source_an_operator_enabled_is_not_re_disabled(self):
        seed.seed_sources()
        frappe.db.set_value("CH Competitor Source", "Budli", "disabled", 0)
        seed.seed_sources()
        self.assertEqual(frappe.db.get_value("CH Competitor Source", "Budli", "disabled"), 0)


class TestItemLinks(SeedHarness):
    def test_a_link_is_created_once(self):
        if not self.item:
            self.skipTest("no enabled Mobiles item on this site")
        seed.seed_sources()
        self.assertEqual(seed.seed_item_links([self.link_spec()])["created"], 1)
        self.assertEqual(seed.seed_item_links([self.link_spec()]),
                         {"created": 0, "skipped": 1})

    def test_a_link_to_an_unknown_item_is_skipped(self):
        seed.seed_sources()
        self.assertEqual(seed.seed_item_links([self.link_spec("NO-SUCH-ITEM-XYZ")]),
                         {"created": 0, "skipped": 1})

    def test_a_link_to_an_unseeded_source_is_skipped(self):
        if not self.item:
            self.skipTest("no enabled Mobiles item on this site")
        self.assertEqual(seed.seed_item_links([self.link_spec(competitor="Nobody")]),
                         {"created": 0, "skipped": 1})


class TestSettings(SeedHarness):
    def test_collection_is_never_switched_on(self):
        before = frappe.db.get_single_value("CH Item Master Settings", "competitor_collection_enabled")
        seed.after_migrate()
        after = frappe.db.get_single_value("CH Item Master Settings", "competitor_collection_enabled")
        self.assertEqual(before, after)

    def test_unset_operational_settings_are_filled_then_left_alone(self):
        frappe.db.set_single_value("CH Item Master Settings", "competitor_data_max_age_days", 0)
        self.assertIn("competitor_data_max_age_days", seed.seed_settings()["filled"])
        frappe.db.set_single_value("CH Item Master Settings", "competitor_data_max_age_days", 3)
        seed.seed_settings()
        self.assertEqual(
            frappe.db.get_single_value("CH Item Master Settings", "competitor_data_max_age_days"), 3)

    def test_after_migrate_runs_every_step_and_reports(self):
        summary = seed.after_migrate()
        self.assertEqual(set(summary), {"sources", "settings", "item_links"})
        self.assertNotIn("failed", summary.values())
