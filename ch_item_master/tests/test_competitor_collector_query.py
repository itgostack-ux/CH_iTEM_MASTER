from unittest import TestCase
from unittest.mock import patch

from ch_item_master.ch_item_master.competitor_pricing import collector


class TestCompetitorCollectorQuery(TestCase):
    @patch.object(collector, "get_setting", return_value=1)
    @patch.object(collector, "get_int_setting", return_value=1000)
    @patch.object(collector.frappe, "get_all", return_value=[])
    def test_scheduler_uses_frappe_v16_safe_stalest_first_order(
        self, get_all, _get_int_setting, _get_setting
    ):
        self.assertEqual(collector.collect_competitor_prices(), [])

        _args, kwargs = get_all.call_args
        self.assertEqual(kwargs["order_by"], "last_run_at asc, name asc")
        self.assertNotIn("(", kwargs["order_by"])

