import json
from pathlib import Path
from unittest import TestCase


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent


class TestReleaseScaleAndConcurrencyGuards(TestCase):
	def test_item_tax_append_locks_parent_items_before_child_indexing(self):
		source = (
			PACKAGE_ROOT
			/ "ch_item_master/doctype/ch_sub_category/ch_sub_category.py"
		).read_text()
		lock_position = source.index("ORDER BY name\n\t\t\tFOR UPDATE")
		max_index_position = source.index("SELECT parent, MAX(idx) AS max_idx")
		self.assertLess(lock_position, max_index_position)

	def test_high_volume_customer_and_report_paths_are_batched(self):
		customer_360 = (PACKAGE_ROOT / "ch_customer_master/customer_360_api.py").read_text()
		customer_dashboard = (
			PACKAGE_ROOT
			/ "ch_customer_master/page/ch_customer_dashboard/ch_customer_dashboard.py"
		).read_text()
		model_report = (
			PACKAGE_ROOT
			/ "ch_item_master/report/model_coverage/model_coverage.py"
		).read_text()
		category_report = (
			PACKAGE_ROOT
			/ "ch_item_master/report/category_manager_report/category_manager_report.py"
		).read_text()
		self.assertIn("ROW_NUMBER() OVER", customer_360)
		self.assertIn("customer_360_device_limit", customer_360)
		self.assertEqual(customer_dashboard.count("for month_range in month_ranges"), 1)
		trend = customer_dashboard[customer_dashboard.index("def _get_revenue_trend"):]
		self.assertEqual(trend.split("def _get_referral_stats", 1)[0].count("frappe.db.sql("), 3)
		self.assertIn("item_counts_by_model", model_report)
		self.assertNotIn("def _get_variant_spec_info", model_report)
		self.assertIn("prices_by_model", category_report)
		self.assertNotIn("for row in models:\n        # Count items", category_report)

	def test_release_scaffolds_are_not_shipped(self):
		obsolete = (
			"_e2e_fixes_20260707.py",
			"_e2e_gift_delivery_mode.py",
			"_e2e_gift_template_variant.py",
			"ch_core/sample_location_hierarchy.py",
			"ch_item_master/seed_market_offers.py",
			"patches/v16_dev_provision_full_topology.py",
			"patches/v16_dev_purge_legacy_bins.py",
		)
		for relative_path in obsolete:
			with self.subTest(path=relative_path):
				self.assertFalse((PACKAGE_ROOT / relative_path).exists())

	def test_scale_limits_are_declarative(self):
		settings = json.loads((
			PACKAGE_ROOT
			/ "ch_item_master/doctype/ch_item_master_settings/ch_item_master_settings.json"
		).read_text())
		fields = {row.get("fieldname") for row in settings["fields"]}
		self.assertTrue({
			"exception_query_limit",
			"interactive_report_row_limit",
			"interactive_report_related_row_limit",
			"customer_360_device_limit",
			"customer_360_lifecycle_log_limit",
			"customer_360_related_row_limit",
			"ready_reckoner_related_row_limit",
			"ready_reckoner_export_limit",
		}.issubset(fields))

	def test_demo_bin_patch_is_atomic_and_paged(self):
		source = (PACKAGE_ROOT / "patches/v16_add_demo_bin.py").read_text()
		self.assertIn("iter_all_rows", source)
		self.assertNotIn("frappe.db.commit", source)
		self.assertNotIn("except Exception", source)
