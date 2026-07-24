import hashlib
import hmac
import inspect
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ch_item_master.ch_core import whatsapp_webhook
from ch_item_master.ch_item_master.doctype.active_vas_plans import active_vas_plans
from ch_item_master.ch_item_master.page.imei_tracker import imei_tracker_api


class TestVASRevenueGuards(TestCase):
	def test_warranty_view_role_is_checked_before_plan_query(self):
		with (
			patch.object(
				active_vas_plans,
				"require_role_setting",
				side_effect=frappe.PermissionError,
			),
			patch.object(active_vas_plans.frappe, "get_all") as get_all,
			self.assertRaises(frappe.PermissionError),
		):
			active_vas_plans._load_authorized_active_plans("SER-1")
		get_all.assert_not_called()

	def test_manual_recognition_checks_configured_role_before_loading_plan(self):
		with (
			patch.object(
				active_vas_plans,
				"require_role_setting",
				side_effect=frappe.PermissionError,
			),
			patch.object(active_vas_plans.frappe.db, "get_value") as get_value,
			self.assertRaises(frappe.PermissionError),
		):
			active_vas_plans.recognize_revenue_now("VAS-PLAN-1")
		get_value.assert_not_called()

	def test_manual_recognition_locks_and_scopes_named_plan(self):
		doc = SimpleNamespace(
			name="VAS-PLAN-1",
			docstatus=1,
			status="Active",
			serial_no="SER-1",
			recognize_revenue_up_to=Mock(return_value={"delta": 10}),
		)
		anchor = {"company": "Company A", "warehouse": "Stores - A", "store": "Store A"}
		with (
			patch.object(active_vas_plans, "require_role_setting") as role_gate,
			patch.object(active_vas_plans.frappe.db, "get_value", return_value=doc.name) as lock,
			patch.object(active_vas_plans.frappe, "get_doc", return_value=doc),
			patch.object(active_vas_plans, "_require_named_permission") as named_permission,
			patch.object(active_vas_plans.frappe, "has_permission", return_value=True) as permission,
			patch.object(active_vas_plans, "_resolve_serial_location", return_value=anchor),
			patch.object(active_vas_plans, "_assert_exact_serial_scope") as scope_guard,
			patch.object(active_vas_plans, "nowdate", return_value="2026-07-22"),
		):
			result = active_vas_plans.recognize_revenue_now(doc.name)

		self.assertEqual(result, {"delta": 10})
		role_gate.assert_called_once()
		self.assertTrue(lock.call_args.kwargs["for_update"])
		named_permission.assert_called_once_with("Active VAS Plans", doc.name, "write")
		self.assertEqual(permission.call_count, 2)
		scope_guard.assert_called_once()
		doc.recognize_revenue_up_to.assert_called_once_with(
			"2026-07-22", row_locked=True
		)

	def test_revenue_post_uses_standard_insert_submit_and_plan_save(self):
		class Plan:
			name = "VAS-PLAN-1"
			company = "Company A"
			start_date = "2026-01-01"
			end_date = "2026-01-11"
			plan_price = 100
			revenue_recognized_amount = 0
			status = "Active"
			revenue_last_recognized_on = None

			def _resolve_recognition_accounts(self):
				return "Deferred - A", "Revenue - A"

		plan = Plan()
		plan.save = Mock()

		class JournalEntry:
			def __init__(self):
				self.name = "JV-1"
				self.flags = frappe._dict()
				self.accounts = []
				self.insert = Mock()
				self.submit = Mock()

			def append(self, fieldname, value):
				self.accounts.append(value)

		journal_entry = JournalEntry()

		def get_value(doctype, *args, **kwargs):
			if doctype == "Account":
				return ("Company A", 0, 0)
			if doctype == "Journal Entry":
				return None
			raise AssertionError(doctype)

		with (
			patch.object(active_vas_plans.frappe.db, "get_value", side_effect=get_value),
			patch.object(active_vas_plans.frappe, "new_doc", return_value=journal_entry),
		):
			result = active_vas_plans.ActiveVASPlans.recognize_revenue_up_to(
				plan, "2026-01-06", row_locked=True
			)

		self.assertEqual(result["delta"], 50)
		journal_entry.insert.assert_called_once_with()
		journal_entry.submit.assert_called_once_with()
		self.assertFalse(journal_entry.flags.get("ignore_permissions"))
		plan.save.assert_called_once_with()


class TestWhatsAppAccountBinding(TestCase):
	def _signed_request(self, body, secret):
		signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
		return (
			patch.object(
				whatsapp_webhook.frappe,
				"request",
				SimpleNamespace(method="POST", get_data=Mock(return_value=body)),
			),
			patch.object(
				whatsapp_webhook.frappe,
				"get_request_header",
				return_value=signature,
			),
		)

	def test_omitted_company_resolves_from_the_only_matching_account(self):
		body = b'{"status":"delivered"}'
		request_patch, header_patch = self._signed_request(body, "secret-a")
		credentials = [
			{"account": "WA-A", "company": "Company A", "secret": "secret-a", "token": "a"},
			{"account": "WA-B", "company": "Company B", "secret": "secret-b", "token": "b"},
		]
		with (
			request_patch,
			header_patch,
			patch.object(whatsapp_webhook, "_configured_webhook_credentials", return_value=credentials),
		):
			self.assertEqual(whatsapp_webhook._authenticate_webhook(), "Company A")

	def test_shared_secret_across_companies_is_rejected_as_ambiguous(self):
		body = b'{"status":"delivered"}'
		request_patch, header_patch = self._signed_request(body, "shared")
		credentials = [
			{"account": "WA-A", "company": "Company A", "secret": "shared", "token": "a"},
			{"account": "WA-B", "company": "Company B", "secret": "shared", "token": "b"},
		]
		with (
			request_patch,
			header_patch,
			patch.object(whatsapp_webhook, "_configured_webhook_credentials", return_value=credentials),
			self.assertRaises(frappe.AuthenticationError),
		):
			whatsapp_webhook._authenticate_webhook()

	def test_payload_cannot_override_authenticated_company(self):
		with (
			patch.object(
				whatsapp_webhook.frappe,
				"request",
				SimpleNamespace(method="POST"),
			),
			patch.object(whatsapp_webhook, "_authenticate_webhook", return_value="Company A"),
			patch.object(
				whatsapp_webhook,
				"_request_payload",
				return_value={"company": "Company B", "status": "delivered"},
			),
			patch.object(whatsapp_webhook, "process_status_event") as process,
			self.assertRaises(frappe.AuthenticationError),
		):
			inspect.unwrap(whatsapp_webhook.gallabox_webhook)()
		process.assert_not_called()

	def test_status_processing_requires_resolved_company(self):
		with self.assertRaises(frappe.AuthenticationError):
			whatsapp_webhook.process_status_event({"status": "delivered"})


class TestIMEIHistoryGuards(TestCase):
	def test_view_role_is_checked_before_serial_lookup(self):
		with (
			patch.object(imei_tracker_api, "has_role_setting", return_value=False),
			patch.object(imei_tracker_api.frappe.db, "get_value") as get_value,
			self.assertRaises(frappe.PermissionError),
		):
			imei_tracker_api.get_imei_history(serial_no="SER-1")
		get_value.assert_not_called()

	def test_out_of_scope_warehouse_stops_before_history_queries(self):
		lifecycle = frappe._dict({
			"name": "SER-1",
			"serial_no": "SER-1",
			"current_company": "Company A",
			"current_warehouse": "Stores - A",
			"current_store": "Store A",
		})
		serial = frappe._dict({"name": "SER-1", "warehouse": "Stores - A"})
		warehouse = frappe._dict({"company": "Company A", "disabled": 0})

		def get_value(doctype, filters, fields=None, **kwargs):
			if doctype == "CH Serial Lifecycle" and isinstance(filters, dict):
				return "SER-1"
			if doctype == "CH Serial Lifecycle":
				return lifecycle
			if doctype == "Serial No":
				return serial
			if doctype == "Warehouse":
				return warehouse
			raise AssertionError((doctype, filters, fields))

		def deny_scope(filters, strict=False):
			self.assertTrue(strict)
			filters["_allowed_warehouses"] = ["Stores - B"]

		with (
			patch.object(imei_tracker_api, "_require_imei_view_access"),
			patch.object(imei_tracker_api, "_require_named_read"),
			patch.object(imei_tracker_api, "is_privileged_user", return_value=False),
			patch.object(imei_tracker_api.frappe.db, "get_value", side_effect=get_value),
			patch.object(imei_tracker_api, "_apply_scope", side_effect=deny_scope),
			patch.object(imei_tracker_api.frappe.db, "sql") as sql,
			self.assertRaises(frappe.PermissionError),
		):
			imei_tracker_api.get_imei_history(serial_no="SER-1")
		sql.assert_not_called()
