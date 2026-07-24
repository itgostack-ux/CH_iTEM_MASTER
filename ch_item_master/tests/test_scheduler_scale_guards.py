import ast
import inspect
import json
import textwrap
from pathlib import Path
from unittest import TestCase

from ch_item_master.ch_customer_master.doctype.ch_loyalty_transaction import ch_loyalty_transaction
from ch_item_master.ch_core import bin_transfer, location_hierarchy
from ch_item_master.ch_item_master import (
	commercial_api,
	exception_api,
	ready_reckoner_api,
	scheduled_tasks,
	voucher_api,
	warranty_api,
)
from ch_item_master.ch_item_master.doctype.ch_warranty_plan import ch_warranty_plan
from ch_item_master.ch_item_master.doctype.ch_coupon_campaign import ch_coupon_campaign
from ch_item_master.ch_item_master.doctype.active_vas_plans import active_vas_plans
from ch_item_master.supplier_scheme import scheduled as supplier_scheduled


class TestSchedulerScaleGuards(TestCase):
	@staticmethod
	def _loop_body_database_calls(function):
		tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
		calls = []
		for loop in (node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))):
			for statement in loop.body:
				for call in (node for node in ast.walk(statement) if isinstance(node, ast.Call)):
					name = ast.unparse(call.func)
					if name in {"frappe.get_all", "frappe.get_list"} or name.startswith("frappe.db."):
						calls.append((call.lineno, name))
		return calls

	def test_registered_scheduler_mutations_do_not_commit_the_request_transaction(self):
		functions = (
			scheduled_tasks.auto_expire_records,
			ch_loyalty_transaction.expire_loyalty_points,
			commercial_api.run_channel_parity_check,
			commercial_api.run_tag_auto_repricing,
			voucher_api.expire_vouchers,
			ch_coupon_campaign.expire_campaigns,
			exception_api.escalate_pending_exceptions,
			exception_api.expire_stale_exceptions,
			warranty_api.expire_sold_plans,
			active_vas_plans.recognize_vas_revenue_monthly,
			supplier_scheduled.auto_close_expired_schemes,
			supplier_scheduled.send_expiry_claim_reminders,
		)
		for function in functions:
			with self.subTest(function=function.__name__):
				self.assertNotIn("frappe.db.commit(", inspect.getsource(function))

	def test_high_volume_jobs_use_configured_batch_limits(self):
		for function in (
			scheduled_tasks.auto_expire_records,
			ch_loyalty_transaction.expire_loyalty_points,
			voucher_api.expire_vouchers,
			ch_coupon_campaign.expire_campaigns,
			exception_api.escalate_pending_exceptions,
			exception_api.expire_stale_exceptions,
			warranty_api.expire_sold_plans,
			active_vas_plans.recognize_vas_revenue_monthly,
		):
			with self.subTest(function=function.__name__):
				self.assertIn("scheduler_batch_limit", inspect.getsource(function))

		for function in (
			commercial_api.run_channel_parity_check,
			commercial_api.run_tag_auto_repricing,
		):
			with self.subTest(function=function.__name__):
				self.assertIn("commercial_scheduler_batch_limit", inspect.getsource(function))

	def test_bulk_eligible_jobs_use_set_based_updates(self):
		for function in (
			scheduled_tasks.auto_expire_records,
			voucher_api.expire_vouchers,
			ch_coupon_campaign.expire_campaigns,
		):
			source = inspect.getsource(function)
			self.assertIn("UPDATE `tab", source)
			self.assertNotIn("frappe.db.set_value", source)

	def test_loyalty_expiry_has_real_date_predicate_and_locked_batch(self):
		source = inspect.getsource(ch_loyalty_transaction.expire_loyalty_points)
		self.assertIn("`expiry_date` <= %(today)s", source)
		self.assertIn("FOR UPDATE", source)
		self.assertIn("reference_name", source)

	def test_auto_repricing_is_idempotent_per_tag_and_price(self):
		source = inspect.getsource(commercial_api.run_tag_auto_repricing)
		self.assertIn("automation_reference", source)
		self.assertIn("SHA2", source)
		self.assertIn("bulk_insert", source)
		self.assertNotIn("frappe.db.exists", source)

	def test_delegation_resolution_is_date_bounded_and_deterministic(self):
		source = inspect.getsource(exception_api._apply_delegation)
		self.assertIn("valid_from", source)
		self.assertIn("valid_to", source)
		self.assertIn("LIMIT 1", source)
		self.assertNotIn("frappe.get_all", source)

	def test_interactive_location_and_warranty_render_loops_are_preloaded(self):
		for function in (
			location_hierarchy.get_company_location_tree,
			warranty_api.get_customer_warranty_dashboard,
		):
			with self.subTest(function=function.__name__):
				self.assertEqual(self._loop_body_database_calls(function), [])
		self.assertIn("plans_by_serial", inspect.getsource(warranty_api.get_customer_warranty_dashboard))
		self.assertIn("claims_by_serial", inspect.getsource(warranty_api.get_customer_warranty_dashboard))

	def test_legacy_stock_backfill_is_single_store_bounded_and_atomic(self):
		source = inspect.getsource(bin_transfer.backfill_existing_stock_to_sellable)
		self.assertIn("if not store:", source)
		self.assertIn('get_int_setting("bin_query_limit"', source)
		self.assertIn("savepoint", source)
		self.assertNotIn("limit_page_length=0", source)
		self.assertNotIn("frappe.db.commit", source)

	def test_plan_applicability_and_ready_reckoner_counts_are_bounded(self):
		plan_source = inspect.getsource(ch_warranty_plan.CHWarrantyPlan.get_applicable_plans)
		self.assertIn("warranty_plan_candidate_limit", plan_source)
		self.assertIn("warranty_plan_result_limit", plan_source)
		self.assertEqual(
			self._loop_body_database_calls(ch_warranty_plan.CHWarrantyPlan.get_applicable_plans),
			[],
		)
		self.assertIn("MAX(item_group", plan_source)
		self.assertIn("MAX(channel", plan_source)
		count_source = inspect.getsource(ready_reckoner_api._count_ready_reckoner_items)
		self.assertIn("SELECT COUNT(*)", count_source)
		self.assertNotIn("frappe.get_all", count_source)

	def test_scheduler_settings_and_automation_reference_are_declared(self):
		package_root = Path(__file__).resolve().parents[1]
		settings = json.loads((
			package_root
			/ "ch_item_master/doctype/ch_item_master_settings/ch_item_master_settings.json"
		).read_text())
		fieldnames = {field.get("fieldname") for field in settings["fields"]}
		self.assertTrue({
			"scheduler_batch_limit",
			"commercial_scheduler_batch_limit",
			"commercial_alert_item_limit",
			"exception_expiry_hours",
		}.issubset(fieldnames))

		change_log = json.loads((
			package_root
			/ "ch_item_master/doctype/ch_price_change_log/ch_price_change_log.json"
		).read_text())
		fields = {field.get("fieldname"): field for field in change_log["fields"]}
		self.assertIn("automation_reference", fields)
		self.assertIn("Tag Auto-Reprice", fields["source"]["options"])
