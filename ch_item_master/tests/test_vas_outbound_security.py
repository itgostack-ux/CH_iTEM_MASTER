from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ch_item_master.outbound_security import (
	parse_exact_host_allowlist,
	post_json_with_credentials,
	validate_allowed_https_url,
)


class TestVASOutboundSecurity(FrappeTestCase):
	def test_endpoint_requires_exact_allowlisted_https_host(self):
		hosts = parse_exact_host_allowlist("api.razorpay.com", label="Razorpay")
		for endpoint in (
			"http://api.razorpay.com/v1/orders",
			"https://user:secret@api.razorpay.com/v1/orders",
			"https://api.razorpay.com.evil.example/v1/orders",
		):
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				validate_allowed_https_url(endpoint, hosts, label="Razorpay", resolve_dns=False)

	@patch("ch_item_master.outbound_security.socket.getaddrinfo")
	def test_private_resolution_is_rejected(self, getaddrinfo):
		getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
		with self.assertRaises(frappe.PermissionError):
			validate_allowed_https_url(
				"https://api.razorpay.com/v1/orders",
				{"api.razorpay.com"},
				label="Razorpay",
			)

	@patch("ch_item_master.outbound_security.socket.getaddrinfo")
	@patch("ch_item_master.outbound_security.requests.post")
	def test_gateway_request_disables_redirects_and_limits_response(self, post, getaddrinfo):
		getaddrinfo.return_value = [(2, 1, 6, "", ("8.8.8.8", 443))]
		response = Mock()
		response.status_code = 200
		response.headers = {}
		response.iter_content.return_value = [b'{"id":"order_1"}']
		post.return_value = response

		result = post_json_with_credentials(
			"https://api.razorpay.com/v1/orders",
			allowed_hosts={"api.razorpay.com"},
			label="Razorpay",
			payload={"amount": 100},
			timeout=10,
			max_response_bytes=1024,
			auth=("key", "secret"),
		)

		self.assertEqual(result, {"id": "order_1"})
		self.assertFalse(post.call_args.kwargs["allow_redirects"])
		self.assertTrue(post.call_args.kwargs["stream"])

	@patch("ch_item_master.outbound_security.socket.getaddrinfo")
	@patch("ch_item_master.outbound_security.requests.post")
	def test_oversized_gateway_response_fails_closed(self, post, getaddrinfo):
		getaddrinfo.return_value = [(2, 1, 6, "", ("8.8.8.8", 443))]
		response = Mock()
		response.status_code = 200
		response.headers = {}
		response.iter_content.return_value = [b"x" * 1025]
		post.return_value = response

		with self.assertRaises(frappe.ValidationError):
			post_json_with_credentials(
				"https://api.razorpay.com/v1/orders",
				allowed_hosts={"api.razorpay.com"},
				label="Razorpay",
				payload={},
				timeout=10,
				max_response_bytes=1024,
			)

	def test_security_settings_are_system_manager_write_only(self):
		for doctype, restricted_role in (
			("CH Item Master Settings", "CH Master Manager"),
			("CH VAS Settings", "CH Warranty Manager"),
		):
			permissions = frappe.get_meta(doctype).permissions
			self.assertTrue(any(row.role == "System Manager" and row.write for row in permissions))
			self.assertFalse(any(row.role == restricted_role and row.write for row in permissions))
