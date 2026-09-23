"""Regression guards for the Item Master Dashboard.

The page was reported as broken -- permanently stuck on "Loading dashboard...".
Nothing was broken: get_dashboard_data answered correctly, it just took 24.3s
over HTTP because `tabItem`.`ch_model` had no index, and every model->item
rollup on the page joins on it. v3_lifecycle_and_indexes indexed ch_category
and ch_sub_category but missed ch_model; v41_index_item_ch_model adds it.

The index is what the first test pins. A timing assertion would be the obvious
test to write here and is deliberately not written: this bench is shared by
concurrent sessions and a long seeder transaction makes wall-clock assertions
flake. The index is the deterministic fact -- if it is present the rollups are
sub-second, and if someone drops it (a Customize Form reload of Item will) this
fails loudly instead of the page quietly going slow again.
"""

import unittest

import frappe


class TestItemMasterDashboard(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self._savepoint = "item_master_dashboard_test"
        frappe.db.savepoint(self._savepoint)

    def tearDown(self):
        frappe.db.rollback(save_point=self._savepoint)

    def test_item_ch_model_is_indexed(self):
        """Every model->item rollup on the dashboard joins Item on ch_model."""
        indexes = frappe.db.sql(
            "SHOW INDEX FROM `tabItem` WHERE Column_name = 'ch_model'", as_dict=True
        )
        self.assertTrue(
            indexes,
            "tabItem.ch_model has no index -- the Item Master Dashboard rollups "
            "become a full scan per CH Model and the page takes ~24s. Run "
            "ch_item_master.patches.v41_index_item_ch_model.",
        )

    def test_get_dashboard_data_returns_every_section(self):
        """The page renders each of these unconditionally; a missing key is a TypeError."""
        from ch_item_master.ch_item_master.page.ch_item_master_dashboard.ch_item_master_dashboard import (
            get_dashboard_data,
        )

        data = get_dashboard_data()
        for key in (
            "company",
            "kpis",
            "alerts",
            "insights",
            "coverage",
            "pricing_health",
            "category_summary",
            "recent_activity",
            "channel_comparison",
        ):
            self.assertIn(key, data)

        for kpi in (
            "total_categories",
            "total_sub_categories",
            "total_models",
            "total_items",
            "active_prices",
            "active_offers",
        ):
            self.assertIn(kpi, data["kpis"])
            self.assertIsInstance(data["kpis"][kpi], int)

    def test_company_filter_narrows_the_price_count(self):
        """The toolbar filter must actually reach the query.

        The filter was invisible for a different reason (page.main.html() wiped
        Frappe's .page-form), so this pins the server half: a company that owns
        no CH Item Price must report zero, not the estate-wide total.
        """
        from ch_item_master.ch_item_master.page.ch_item_master_dashboard.ch_item_master_dashboard import (
            get_dashboard_data,
        )

        priced = frappe.db.sql(
            """SELECT company, COUNT(*) AS n FROM `tabCH Item Price`
               WHERE status = 'Active' AND COALESCE(company, '') <> ''
               GROUP BY company ORDER BY n DESC LIMIT 1""",
            as_dict=True,
        )
        if not priced:
            raise unittest.SkipTest("no company owns an Active CH Item Price on this site")

        owner = priced[0].company
        other = frappe.db.get_value(
            "Company", {"name": ("!=", owner)}, "name"
        )
        if not other:
            raise unittest.SkipTest("only one Company on this site")

        self.assertEqual(get_dashboard_data(company=owner)["kpis"]["active_prices"], priced[0].n)
        self.assertEqual(get_dashboard_data(company=other)["kpis"]["active_prices"], 0)
