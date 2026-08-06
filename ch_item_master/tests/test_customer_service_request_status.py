from unittest import TestCase
from unittest.mock import patch

import frappe

from ch_item_master.ch_customer_master import customer_360_api, customer_portal_api
from ch_item_master.ch_customer_master.page.ch_customer_dashboard import ch_customer_dashboard


class TestCustomerServiceRequestStatusContract(TestCase):
	def test_customer_dashboard_reads_canonical_decision(self):
		def get_all(doctype, **kwargs):
			if doctype != "Service Request":
				return []
			self.assertIn("decision as status", kwargs["fields"])
			return [frappe._dict({
				"name": "SR-TEST",
				"customer_name": "Test Customer",
				"status": "In Service",
				"company": "Test Company",
				"creation": "2026-08-06 10:00:00",
				"owner": "Administrator",
			})]

		def exists(doctype, name):
			return doctype == "DocType" and name == "Service Request"

		with (
			patch.object(ch_customer_dashboard, "get_int_setting", return_value=7),
			patch.object(ch_customer_dashboard.frappe, "get_all", side_effect=get_all),
			patch.object(ch_customer_dashboard.frappe.db, "exists", side_effect=exists),
		):
			activity = ch_customer_dashboard._get_recent_activity("Test Company")

		self.assertEqual(activity[0]["description"], "Service: Test Customer (In Service)")

	def test_customer_360_preserves_status_response_key(self):
		def get_all(doctype, **kwargs):
			if doctype != "Service Request":
				return []
			self.assertIn("decision as status", kwargs["fields"])
			return [frappe._dict({
				"name": "SR-TEST",
				"creation": "2026-08-06 10:00:00",
				"company": "Test Company",
				"status": "Draft",
				"device_item_name": "Test Phone",
			})]

		def exists(doctype, name):
			return doctype == "DocType" and name == "Service Request"

		with (
			patch.object(customer_360_api.frappe, "get_all", side_effect=get_all),
			patch.object(customer_360_api.frappe.db, "exists", side_effect=exists),
		):
			transactions = customer_360_api._get_recent_transactions(
				"Test Customer", "Test Company"
			)

		self.assertEqual(transactions[0]["status"], "Draft")
		self.assertEqual(transactions[0]["type"], "Service")

	def test_customer_portal_uses_same_compatibility_alias(self):
		import inspect

		source = inspect.getsource(customer_portal_api.get_dashboard)
		self.assertIn('"decision as status"', source)
		self.assertNotIn('"creation", "status", "device_item_name"', source)
