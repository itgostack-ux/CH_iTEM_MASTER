from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ch_item_master import config, security
from ch_item_master.ch_core.doctype.ch_otp_log import ch_otp_log
from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
from ch_item_master.ch_core import whatsapp_webhook
from ch_item_master.ch_customer_master import customer_portal_api
from ch_item_master.ch_item_master import rbac
from ch_item_master.ch_core.whatsapp_webhook import _valid_webhook_signature
from ch_item_master.ch_item_master import exception_api
from ch_item_master.ch_item_master.doctype.ch_exception_request.ch_exception_request import (
	CHExceptionRequest,
)
from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
	CHWarrantyClaim,
)
from ch_item_master.ch_item_master.warranty_api import (
	_processing_fee_link_signature,
	_validate_processing_fee_link,
)


class TestReleaseAuthorizationRegressions(TestCase):
	def test_administrator_is_immutable_privileged_user(self):
		with patch.object(config, "get_setting", return_value="CH Viewer"):
			self.assertTrue(config.is_privileged_user("Administrator"))
			self.assertTrue(config.has_role_setting("app_access_roles", user="Administrator"))

	def test_system_manager_cannot_be_removed_from_role_setting(self):
		with (
			patch.object(config, "get_setting", return_value="CH Viewer"),
			patch.object(config.frappe, "get_roles", return_value=["System Manager"]),
		):
			self.assertIn("System Manager", config.get_role_setting("app_access_roles"))
			self.assertTrue(config.has_role_setting("app_access_roles", user="manager@example.com"))

	def test_empty_company_scope_generates_deny_query(self):
		with patch.object(security, "get_user_allowed_companies", return_value=[]):
			self.assertEqual(
				security.get_company_permission_query("CH Warranty Claim", user="unscoped@example.com"),
				"1=0",
			)

	def test_client_cannot_supply_another_exception_approver(self):
		request = SimpleNamespace(assigned_approver=None)
		original_user = frappe.session.user
		try:
			frappe.set_user("approver@example.com")
			with self.assertRaises(frappe.PermissionError):
				CHExceptionRequest._validate_approver(request, "victim@example.com")
		finally:
			frappe.set_user(original_user)

	def test_exception_company_scope_uses_exact_configured_names(self):
		request = SimpleNamespace(exception_type="Price Override", company="GoFix Demo Company")
		exception_type = frappe._dict({"enabled": 1, "applicable_to_ggr": 1, "applicable_to_gfs": 0})
		with (
			patch.object(frappe, "get_cached_doc", return_value=exception_type),
			patch(
				"ch_item_master.ch_item_master.doctype.ch_exception_request.ch_exception_request.get_list_setting",
				return_value=frozenset(),
			),
		):
			CHExceptionRequest._validate_exception_type(request)

		def company_setting(fieldname):
			return frozenset({"GoFix Demo Company"}) if fieldname == "exception_gfs_companies" else frozenset()

		with (
			patch.object(frappe, "get_cached_doc", return_value=exception_type),
			patch(
				"ch_item_master.ch_item_master.doctype.ch_exception_request.ch_exception_request.get_list_setting",
				side_effect=company_setting,
			),
			self.assertRaises(frappe.ValidationError),
		):
			CHExceptionRequest._validate_exception_type(request)

	def test_otp_id_uses_the_atomic_document_series(self):
		doc = SimpleNamespace(naming_series="CHOTP-.#####", name=None, otp_id=None)
		with patch.object(ch_otp_log, "make_autoname", return_value="CHOTP-00042"):
			CHOTPLog.autoname(doc)

		self.assertEqual(doc.name, "CHOTP-00042")
		self.assertEqual(doc.otp_id, 42)

	def test_otp_generation_fails_closed_when_lock_is_not_acquired(self):
		with (
			patch.object(ch_otp_log.frappe.db, "sql", return_value=[(0,)]) as sql,
			self.assertRaises(frappe.ValidationError),
		):
			CHOTPLog.generate_otp("9876543210", "Discount Override")

		self.assertEqual(sql.call_count, 1)

	def test_otp_generation_uses_secure_digit_selection(self):
		doc = Mock()
		with (
			patch.object(ch_otp_log.secrets, "choice", side_effect=iter("123456")) as choice,
			patch.object(ch_otp_log.frappe.db, "sql", return_value=[(1,)]),
			patch.object(ch_otp_log.frappe.db, "count", return_value=0),
			patch.object(ch_otp_log.frappe, "get_doc", return_value=doc),
			patch.object(ch_otp_log.frappe.db, "commit"),
		):
			otp = CHOTPLog.generate_otp("9876543210", "Discount Override")

		self.assertEqual(otp, "123456")
		self.assertEqual(choice.call_count, 6)
		doc.insert.assert_called_once_with(ignore_permissions=True)

	def test_otp_verification_locks_the_pending_row(self):
		doc = Mock()
		doc.expires_at = ch_otp_log.add_to_date(ch_otp_log.now_datetime(), minutes=5)
		doc.attempts = 0
		doc.get_password.return_value = "123456"
		with (
			patch.object(ch_otp_log.frappe.db, "get_value", return_value="CHOTP-00001") as get_value,
			patch.object(ch_otp_log.frappe, "get_doc", return_value=doc),
			patch("ch_item_master.ch_core.shadow_live.master_otp_matches", return_value=False),
			patch.object(ch_otp_log.frappe.db, "commit"),
		):
			result = CHOTPLog.verify_otp("9876543210", "Discount Override", "123456")

		self.assertTrue(result["valid"])
		self.assertTrue(get_value.call_args.kwargs["for_update"])
		self.assertEqual(doc.status, "Verified")

	def test_master_otp_requires_an_active_pending_challenge(self):
		with (
			patch.object(ch_otp_log.frappe.db, "get_value", side_effect=[None, None]),
			patch("ch_item_master.ch_core.shadow_live.master_otp_matches", return_value=True),
		):
			result = CHOTPLog.verify_otp("9876543210", "Discount Override", "999999")

		self.assertFalse(result["valid"])

	def test_consumed_otp_cannot_be_replayed(self):
		with patch.object(ch_otp_log.frappe.db, "get_value", return_value=None) as get_value:
			result = CHOTPLog.verify_otp("9876543210", "Discount Override", "123456")

		self.assertFalse(result["valid"])
		self.assertEqual(get_value.call_count, 1)

	def test_customer_portal_uses_registered_email_not_caller_email(self):
		customer = frappe._dict({
			"name": "CUST-1",
			"customer_name": "Trusted Customer",
			"email_id": "trusted@example.com",
		})
		with (
			patch.object(customer_portal_api, "_atomic_window_counter", return_value=1),
			patch.object(customer_portal_api, "_customer_for_mobile", return_value=customer),
			patch.object(customer_portal_api.CHOTPLog, "generate_otp", return_value="123456") as generate,
			patch("ch_item_master.ch_core.whatsapp.get_whatsapp_settings", return_value=None),
			patch.object(customer_portal_api, "send_otp_email", return_value=True) as send_email,
		):
			result = customer_portal_api.request_login_otp(
				"9876543210",
				email_id="attacker@example.com",
			)

		self.assertTrue(result["sent_email"])
		send_email.assert_called_once_with(
			"trusted@example.com",
			"123456",
			"Customer Portal Login",
			"",
		)
		self.assertEqual(generate.call_args.kwargs["reference_name"], "CUST-1")

	def test_portal_session_claim_is_single_use(self):
		cache = Mock()
		cache.make_key.return_value = b"portal-otp-claim"
		cache.set.side_effect = [True, False]
		cache.return_value = cache
		with (
			patch.object(customer_portal_api.frappe, "cache", cache),
			patch.object(
				customer_portal_api.frappe,
				"generate_hash",
				side_effect=["token-1", "token-2", "exception-id"],
			),
		):
			self.assertEqual(
				customer_portal_api._create_portal_session(
					"9876543210", "CUST-1", otp_log="CHOTP-1"
				),
				"token-1",
			)
			with self.assertRaises(frappe.AuthenticationError):
				customer_portal_api._create_portal_session(
					"9876543210", "CUST-1", otp_log="CHOTP-1"
				)

	def test_item_query_fails_closed_without_company_scope(self):
		with (
			patch.object(rbac, "_has_configured_role", return_value=False),
			patch("ch_item_master.security.get_user_allowed_companies", return_value=[]),
		):
			self.assertEqual(rbac.get_item_query("unscoped@example.com"), "1=0")

	def test_item_query_fails_closed_when_scope_resolution_errors(self):
		with (
			patch.object(rbac, "_has_configured_role", return_value=False),
			patch(
				"ch_item_master.security.get_user_allowed_companies",
				side_effect=RuntimeError("scope unavailable"),
			),
		):
			self.assertEqual(rbac.get_item_query("unscoped@example.com"), "1=0")

	def test_exception_requester_cannot_reject_own_request(self):
		request = SimpleNamespace(
			requested_by="requester@example.com",
			_validate_approver=Mock(return_value="requester@example.com"),
		)
		with (
			patch(
				"ch_item_master.ch_item_master.rbac.check_sod",
				side_effect=frappe.PermissionError,
			) as check_sod,
			self.assertRaises(frappe.PermissionError),
		):
			CHExceptionRequest.reject(request, reason="Not acceptable")

		check_sod.assert_called_once_with(
			submitted_by="requester@example.com",
			approver="requester@example.com",
		)
		self.assertFalse(hasattr(request, "status"))

	def test_whatsapp_webhook_hmac_is_body_bound(self):
		secret = "release-test-secret"
		body = b'{"status":"delivered"}'
		signature = _processing_fee_link_signature(body.decode(), 1, secret)
		self.assertFalse(_valid_webhook_signature(body, signature, secret))

		import hashlib
		import hmac

		valid = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
		self.assertTrue(_valid_webhook_signature(body, valid, secret))
		self.assertFalse(_valid_webhook_signature(b'{"status":"failed"}', valid, secret))

	def test_whatsapp_webhook_fails_closed_without_credentials(self):
		with (
			patch.object(whatsapp_webhook, "_configured_webhook_credentials", return_value=[]),
			self.assertRaises(frappe.AuthenticationError),
		):
			whatsapp_webhook._authenticate_webhook()

	def test_required_exception_otp_cannot_be_omitted(self):
		exception = frappe._dict({
			"status": "Pending",
			"docstatus": 0,
			"exception_type": "Price Override",
			"requested_by": "requester@example.com",
		})
		exception.name = "CHEXC-TEST"
		with (
			patch.object(exception_api.frappe, "get_doc", return_value=exception),
			patch.object(exception_api.frappe, "has_permission", return_value=True),
			patch.object(
				exception_api.frappe,
				"get_cached_doc",
				return_value=frappe._dict({"requires_otp": 1}),
			),
			patch("ch_item_master.ch_item_master.rbac.check_sod"),
			self.assertRaises(frappe.AuthenticationError),
		):
			exception_api.approve_exception(exception.name)

	def test_processing_fee_link_rejects_forgery_and_expiry(self):
		secret = "release-test-link-secret"
		valid = _processing_fee_link_signature("CH-WC-1", 200, secret)
		with (
			patch(
				"ch_item_master.ch_item_master.warranty_api._processing_fee_link_secret",
				return_value=secret,
			),
			patch("ch_item_master.ch_item_master.warranty_api.time.time", return_value=100),
		):
			_validate_processing_fee_link("CH-WC-1", 200, valid)
			with self.assertRaises(frappe.AuthenticationError):
				_validate_processing_fee_link("CH-WC-1", 200, "forged")

		with (
			patch(
				"ch_item_master.ch_item_master.warranty_api._processing_fee_link_secret",
				return_value=secret,
			),
			patch("ch_item_master.ch_item_master.warranty_api.time.time", return_value=201),
			self.assertRaises(frappe.AuthenticationError),
		):
			_validate_processing_fee_link("CH-WC-1", 200, valid)

	def test_fee_link_sender_uses_signed_link_for_positive_fee(self):
		claim = SimpleNamespace(
			docstatus=1,
			processing_fee_status="Pending",
			processing_fee_amount=250,
			name="CH-WC-1",
			claim_status="Fee Pending",
			customer_phone=None,
			customer="CUST-1",
			db_set=Mock(),
			_log=Mock(),
			_send_fee_email=Mock(),
			_send_fee_whatsapp=Mock(),
			_send_fee_sms=Mock(),
			_require_action=Mock(),
		)
		with (
			patch(
				"ch_item_master.ch_item_master.warranty_api.build_processing_fee_link",
				return_value="https://example.test/signed",
			),
			patch.object(frappe, "msgprint"),
		):
			result = CHWarrantyClaim.send_fee_payment_link(claim, channel="Email")

		self.assertEqual(result["link_url"], "https://example.test/signed")
		claim._send_fee_email.assert_called_once_with("https://example.test/signed")
