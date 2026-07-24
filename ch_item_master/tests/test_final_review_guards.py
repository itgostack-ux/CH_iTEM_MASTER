import json
import inspect
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ch_item_master import config
from ch_item_master.ch_item_master import exception_api, import_api, price_approval, rbac, warranty_api
from ch_item_master.ch_item_master.doctype.active_vas_plans import active_vas_plans
from ch_item_master.ch_item_master.page.ch_logistics_hub import ch_logistics_hub
from ch_item_master.supplier_scheme.doctype.scheme_document_upload import scheme_document_upload
from ch_item_master.supplier_scheme.doctype.supplier_scheme_circular import supplier_scheme_circular


class TestFinalReviewGuards(TestCase):
	def test_exposed_mutations_are_post_only(self):
		functions = (
			import_api.import_masters,
			import_api.import_masters_from_csv,
			exception_api.request_exception_otp,
			warranty_api.need_more_info_claim,
			ch_logistics_hub.schedule_pickup,
			active_vas_plans.recognize_revenue_now,
			supplier_scheme_circular.SupplierSchemeCircular.recompute_achievements,
		)
		for function in functions:
			self.assertEqual(
				frappe.allowed_http_methods_for_whitelisted_func[function],
				["POST"],
			)

	def test_price_queue_is_company_scoped_routed_and_bounded(self):
		batch = frappe._dict({
			"name": "BATCH-1",
			"title": "Batch",
			"company": "Company A",
			"submitted_by": "submitter@example.com",
			"submitted_at": "2026-07-22 10:00:00",
		})
		approval = frappe._dict({
			"parent": batch.name,
			"category": "Phones",
			"approver": "approver@example.com",
			"routed_via": "Category Manager",
			"row_count": 2,
			"total_value": 100,
		})
		with (
			patch.object(price_approval.frappe, "session", frappe._dict(user="approver@example.com")),
			patch.object(price_approval, "_is_override", return_value=False),
			patch.object(price_approval, "has_role_setting", return_value=True),
			patch.object(price_approval.frappe, "has_permission", return_value=True),
			patch.object(price_approval, "get_company_filter_value", return_value="Company A"),
			patch.object(price_approval, "get_int_setting", return_value=25),
			patch.object(price_approval.frappe, "get_list", return_value=[batch]) as get_list,
			patch.object(price_approval.frappe, "get_all", return_value=[approval]) as get_all,
		):
			rows = price_approval.get_my_pending_approvals("Company A")

		self.assertEqual(rows[0].batch, batch.name)
		self.assertEqual(get_list.call_args.kwargs["filters"]["company"], "Company A")
		self.assertEqual(get_list.call_args.kwargs["limit_page_length"], 25)
		self.assertEqual(get_all.call_args.kwargs["filters"]["approver"], "approver@example.com")
		self.assertEqual(get_all.call_args.kwargs["limit_page_length"], 25)

	def test_price_queue_override_keeps_all_routed_rows(self):
		batch = frappe._dict({
			"name": "BATCH-1", "title": "Batch", "company": "Company A",
			"submitted_by": "user@example.com", "submitted_at": "2026-07-22 10:00:00",
		})
		with (
			patch.object(price_approval.frappe, "session", frappe._dict(user="Administrator")),
			patch.object(price_approval, "_is_override", return_value=True),
			patch.object(price_approval.frappe, "has_permission", return_value=True),
			patch.object(price_approval, "get_company_filter_value", return_value=None),
			patch.object(price_approval, "get_int_setting", return_value=100),
			patch.object(price_approval.frappe, "get_list", return_value=[batch]),
			patch.object(price_approval.frappe, "get_all", return_value=[]) as get_all,
		):
			self.assertEqual(price_approval.get_my_pending_approvals(), [])

		self.assertNotIn("approver", get_all.call_args.kwargs["filters"])

	def test_notification_users_are_business_scoped_and_bounded(self):
		from ch_erp15.ch_erp15 import notification_router

		with (
			patch.object(config, "get_int_setting", return_value=2),
			patch.object(
				notification_router,
				"get_scoped_users",
				return_value=["z@example.com", "a@example.com", "b@example.com"],
			) as get_scoped_users,
			patch.object(
				notification_router,
				"filter_users_by_company",
				return_value=["z@example.com", "a@example.com", "b@example.com"],
			) as filter_users_by_company,
			patch.object(
				notification_router,
				"filter_business_notification_recipients",
				return_value=["z@example.com", "a@example.com", "b@example.com"],
			),
		):
			users = config.get_enabled_role_users(
				("Business Approver",),
				company="Company A",
				store="Store A",
			)

		self.assertEqual(users, ["a@example.com", "b@example.com"])
		get_scoped_users.assert_called_once_with(["Business Approver"], store="Store A")
		filter_users_by_company.assert_called_once_with(
			["z@example.com", "a@example.com", "b@example.com"],
			"Company A",
		)

	def test_role_expiry_is_bounded_grouped_and_set_based(self):
		records = [
			frappe._dict(name="RA-1", user="user@example.com", role="Role A"),
			frappe._dict(name="RA-2", user="user@example.com", role="Role B"),
		]
		current = [frappe._dict(user="user@example.com", role="Role B")]
		user_doc = MagicMock()
		user_doc.roles = [frappe._dict(role="Role A"), frappe._dict(role="Role B")]
		with (
			patch.object(rbac, "get_int_setting", return_value=2) as get_limit,
			patch.object(rbac, "today", return_value="2026-07-22"),
			patch.object(rbac, "now_datetime", return_value="2026-07-22 00:00:00"),
			patch.object(rbac.frappe.db, "table_exists", return_value=True),
			patch.object(rbac.frappe.db, "sql", side_effect=[records, current, None]) as sql,
			patch.object(rbac.frappe.db, "savepoint") as savepoint,
			patch.object(rbac.frappe.db, "commit") as commit,
			patch.object(rbac.frappe, "get_doc", return_value=user_doc),
		):
			result = rbac.expire_role_assignments()

		get_limit.assert_called_once_with("role_expiry_batch_limit", 500, minimum=1)
		self.assertEqual(result, {"expired": 2, "failed": 0, "has_more": True})
		self.assertEqual([row.role for row in user_doc.roles], ["Role B"])
		user_doc.save.assert_called_once_with(ignore_permissions=True)
		savepoint.assert_called_once_with("role_expiry_0")
		commit.assert_not_called()
		self.assertIn("LIMIT %(batch_limit)s", sql.call_args_list[0].args[0])
		self.assertIn("UPDATE `tabCH Role Assignment`", sql.call_args_list[2].args[0])

	def test_role_expiry_failure_stays_active_for_retry(self):
		records = [frappe._dict(name="RA-1", user="user@example.com", role="Role A")]
		user_doc = MagicMock()
		user_doc.roles = [frappe._dict(role="Role A")]
		user_doc.save.side_effect = RuntimeError("retry me")
		with (
			patch.object(rbac, "get_int_setting", return_value=10),
			patch.object(rbac, "today", return_value="2026-07-22"),
			patch.object(rbac.frappe.db, "table_exists", return_value=True),
			patch.object(rbac.frappe.db, "sql", side_effect=[records, []]) as sql,
			patch.object(rbac.frappe.db, "savepoint"),
			patch.object(rbac.frappe.db, "rollback") as rollback,
			patch.object(rbac.frappe, "get_doc", return_value=user_doc),
			patch.object(rbac.frappe, "log_error"),
		):
			result = rbac.expire_role_assignments()

		self.assertEqual(result, {"expired": 0, "failed": 1, "has_more": False})
		rollback.assert_called_once_with(save_point="role_expiry_0")
		self.assertEqual(sql.call_count, 2)

	def test_supplier_scheme_ai_payload_limit_is_app_configured_and_capped(self):
		with patch.object(scheme_document_upload, "get_int_setting", return_value=12_345) as setting:
			self.assertEqual(scheme_document_upload._max_payload_chars(), 12_345)
		setting.assert_called_once_with(
			"supplier_scheme_ai_payload_char_limit",
			8000,
			minimum=1000,
		)
		with patch.object(scheme_document_upload, "get_int_setting", return_value=2_000_000):
			self.assertEqual(scheme_document_upload._max_payload_chars(), 1_000_000)

	def test_scheduler_and_notification_limits_exist_in_settings(self):
		settings_path = (
			Path(config.__file__).parent
			/ "ch_item_master"
			/ "doctype"
			/ "ch_item_master_settings"
			/ "ch_item_master_settings.json"
		)
		settings = json.loads(settings_path.read_text())
		fieldnames = {field.get("fieldname") for field in settings["fields"]}
		self.assertTrue({
			"notification_recipient_limit",
			"role_expiry_batch_limit",
			"supplier_scheme_ai_payload_char_limit",
			"supplier_scheme_scheduler_batch_limit",
			"break_glass_monitor_batch_limit",
		}.issubset(fieldnames))
		self.assertEqual(
			settings["field_order"],
			[field["fieldname"] for field in settings["fields"] if field.get("fieldname")],
		)
		monitor_source = inspect.getsource(rbac.monitor_break_glass_sessions)
		self.assertIn("limit=batch_limit", monitor_source)
		self.assertNotIn("frappe.db.commit", monitor_source)

	def test_warranty_vas_field_order_matches_declared_fields(self):
		doctypes = {
			"CH Warranty Plan Channel", "Active VAS Plans", "CH Warranty Plan Item Group",
			"CH VAS Settings", "CH Claim Media", "CH VAS Ledger",
			"CH Warranty Plan Category", "CH Warranty Plan", "CH Plan Fee Rule",
			"CH VAS Benefit Rule", "VAS Commission", "CH Claim Issue Category",
			"VAS Partner", "CH Coverage Rule",
		}
		root = Path(active_vas_plans.__file__).parents[2] / "doctype"
		found = set()
		for path in root.glob("*/*.json"):
			data = json.loads(path.read_text())
			if data.get("name") not in doctypes:
				continue
			found.add(data["name"])
			declared = [field["fieldname"] for field in data.get("fields", []) if field.get("fieldname")]
			self.assertEqual(data.get("field_order"), declared, data["name"])
		self.assertEqual(found, doctypes)
