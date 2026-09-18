# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""A catalogue larger than the row limit must still produce a report.

Model Coverage and Category Manager Report both fetched one row past
``interactive_report_row_limit`` and threw `ValidationError` if it came back.
The effect was backwards: the widest categories — the ones a category manager
most wants to look at — were exactly the ones that returned nothing. Measured
on this site, both refused outright while 2,000+ models were waiting.

The bound is kept; the answer changes. Each returns its first page in the order
it already sorts by, plus a message saying the list was cut.

See ch_erp15.tests.test_report_row_limits for the same guard on Stock Shortage
By Store.
"""

from __future__ import annotations

import unittest

import frappe

from ch_item_master.ch_item_master.report.category_manager_report import (
    category_manager_report as category_report,
)
from ch_item_master.ch_item_master.report.model_coverage import model_coverage as coverage_report


class TestInteractiveReportRowLimits(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_model_coverage_returns_rows_instead_of_throwing(self):
        columns, data, message = coverage_report.execute({})
        self.assertTrue(columns)
        self.assertIsInstance(data, list)

    def test_category_manager_returns_rows_instead_of_throwing(self):
        columns, data, message, chart, summary = category_report.execute({})
        self.assertTrue(columns)
        self.assertIsInstance(data, list)

    def test_a_cut_list_says_so(self):
        """A truncated page that stays silent reads as a complete one."""
        from ch_item_master.config import get_int_setting

        limit = min(get_int_setting("interactive_report_row_limit", 2000, minimum=1), 10000)
        checks = (
            ("Model Coverage", coverage_report.execute({})[1], coverage_report.execute({})[2]),
            ("Category Manager Report",
             category_report.execute({})[1], category_report.execute({})[2]),
        )
        tested = 0
        for name, data, message in checks:
            self.assertLessEqual(
                len(data), limit, f"{name} returned {len(data)} rows against a bound of {limit}"
            )
            if len(data) < limit:
                continue
            tested += 1
            self.assertTrue(
                message and str(limit) in message,
                f"{name} cut the list and said nothing about it",
            )
        if not tested:
            self.skipTest("catalogue is smaller than the row limit — nothing to truncate")
