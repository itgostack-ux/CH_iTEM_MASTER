# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ch_item_master.outbound_security import parse_exact_host_allowlist, validate_allowed_https_url


class CHVASSettings(Document):
	def validate(self):
		self.razorpay_allowed_hosts = self.razorpay_allowed_hosts or "api.razorpay.com"
		self.cashfree_allowed_hosts = self.cashfree_allowed_hosts or "api.cashfree.com\nsandbox.cashfree.com"
		self.razorpay_order_api_url = self.razorpay_order_api_url or "https://api.razorpay.com/v1/orders"
		self.cashfree_order_api_url = self.cashfree_order_api_url or "https://api.cashfree.com/pg/orders"
		self.gateway_timeout_seconds = cint(self.gateway_timeout_seconds) or 10
		self.gateway_response_max_bytes = cint(self.gateway_response_max_bytes) or 65536
		if cint(self.gateway_timeout_seconds) < 1 or cint(self.gateway_timeout_seconds) > 60:
			frappe.throw(_("Gateway Timeout must be between 1 and 60 seconds."))
		if cint(self.gateway_response_max_bytes) < 1024 or cint(self.gateway_response_max_bytes) > 1048576:
			frappe.throw(_("Gateway Response Maximum Bytes must be between 1 KB and 1 MB."))

		razorpay_hosts = parse_exact_host_allowlist(self.razorpay_allowed_hosts, label="Razorpay")
		cashfree_hosts = parse_exact_host_allowlist(self.cashfree_allowed_hosts, label="Cashfree")
		validate_allowed_https_url(
			self.razorpay_order_api_url,
			razorpay_hosts,
			label="Razorpay Order API",
			resolve_dns=False,
		)
		validate_allowed_https_url(
			self.cashfree_order_api_url,
			cashfree_hosts,
			label="Cashfree Order API",
			resolve_dns=False,
		)


def get_vas_settings():
	"""Return cached CH VAS Settings singleton.

	Usage:
		from ch_item_master.ch_item_master.doctype.ch_vas_settings.ch_vas_settings import get_vas_settings
		settings = get_vas_settings()
		threshold = settings.anniversary_threshold_months
	"""
	return frappe.get_cached_doc("CH VAS Settings")


def get_warranty_company():
	"""Shortcut: return the warranty issuer company name."""
	return get_vas_settings().gogizmo_company


def get_service_company():
	"""Shortcut: return the service provider company name."""
	return get_vas_settings().gofix_company


def get_fee_waiver_roles():
	"""Return list of roles allowed to approve fee waivers."""
	raw = get_vas_settings().fee_waiver_roles or ""
	return [r.strip() for r in raw.split("\n") if r.strip()]
