import inspect
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ch_item_master.ch_core import whatsapp_webhook
from ch_item_master.ch_item_master import warranty_api
from ch_item_master.ch_item_master.doctype.ch_exception_request import ch_exception_request
from ch_item_master.ch_item_master.doctype.ch_scheme_receivable import ch_scheme_receivable


class TestFinancialExceptionReleaseGuards(TestCase):
	def test_guest_payment_mutations_are_post_only(self):
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[warranty_api.pay_processing_fee],
			["POST"],
		)
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[warranty_api.payment_webhook],
			["POST"],
		)

	def test_whatsapp_get_is_verification_only(self):
		with (
			patch.object(whatsapp_webhook.frappe, "request", SimpleNamespace(method="GET")),
			patch.object(whatsapp_webhook, "_authenticate_webhook"),
			patch.object(whatsapp_webhook, "_request_payload", return_value={"challenge": "verified"}),
			patch.object(whatsapp_webhook, "process_status_event") as process_status,
		):
			self.assertEqual(inspect.unwrap(whatsapp_webhook.gallabox_webhook)(), "verified")
		process_status.assert_not_called()

	def test_whatsapp_status_endpoint_allows_only_get_and_post(self):
		self.assertEqual(
			set(frappe.allowed_http_methods_for_whitelisted_func[whatsapp_webhook.gallabox_webhook]),
			{"GET", "POST"},
		)

	def test_payment_entry_party_must_match_receivable(self):
		receivable = SimpleNamespace(
			name="CHSR-TEST",
			payment_entry=None,
			company="Test Company",
			party_type="Supplier",
			party_name="SUP-001",
		)
		entry = SimpleNamespace(
			doctype="Payment Entry",
			name="ACC-PAY-TEST",
			docstatus=1,
			company="Test Company",
			payment_type="Receive",
			party_type="Supplier",
			party="SUP-OTHER",
			base_received_amount=100,
			reference_no="UTR-1",
			posting_date="2026-07-22",
		)
		with (
			patch.object(ch_scheme_receivable.frappe, "get_doc", return_value=entry),
			patch.object(ch_scheme_receivable, "_require_reference_read"),
			self.assertRaises(frappe.ValidationError),
		):
			ch_scheme_receivable._validate_payment_entry(
				receivable, entry.name, 100, "UTR-1", "2026-07-22"
			)

	def test_scheme_mutation_authorizes_and_locks_before_state_change(self):
		doc = SimpleNamespace(
			name="CHSR-TEST",
			doctype="CH Scheme Receivable",
			docstatus=1,
			status="Settled",
		)
		with (
			patch.object(ch_scheme_receivable.frappe, "get_doc", return_value=doc),
			patch.object(ch_scheme_receivable, "require_scoped_document_action") as authorize,
			self.assertRaises(frappe.ValidationError),
		):
			ch_scheme_receivable.record_settlement(doc.name, 1, payment_reference="UTR-1")
		authorize.assert_called_once_with(
			doc,
			"scheme_receivable_settlement_roles",
			ch_scheme_receivable._SETTLEMENT_ROLES,
			action="record a scheme receivable settlement",
			permission_types=("write",),
			store_field="store",
			lock=True,
		)

	def test_direct_financial_field_mutation_is_rejected(self):
		doc = SimpleNamespace(
			flags={},
			is_new=lambda: True,
			get=lambda field: 1 if field == "received_amount" else None,
		)
		with self.assertRaises(frappe.PermissionError):
			ch_scheme_receivable.CHSchemeReceivable._validate_financial_mutation(doc)

	def test_delivery_note_item_identity_is_server_authoritative(self):
		row = frappe._dict(
			name="DNI-1",
			idx=1,
			item_code="ITEM-1",
			item_name="Item One",
			serial_no="IMEI-1",
			rate=100,
			warehouse="STORE-WH",
		)
		dn = SimpleNamespace(
			doctype="Delivery Note",
			name="DN-TEST",
			company="Test Company",
			customer="CUST-1",
			customer_name="Customer",
			items=[row],
			set_warehouse="STORE-WH",
			get=lambda field: "STORE-WH" if field == "set_warehouse" else None,
		)
		payload = [{
			"row_name": row.name,
			"imei_no": "IMEI-1",
			"item_code": "FORGED-ITEM",
			"exception_amount": 10,
		}]
		with (
			patch.object(ch_exception_request.frappe, "get_doc", return_value=dn),
			patch.object(ch_exception_request, "require_scoped_document_action"),
			patch.object(ch_exception_request, "get_int_setting", side_effect=lambda field, default, minimum=1: default),
			patch.object(ch_exception_request.frappe, "has_permission", return_value=True),
			patch.object(
				ch_exception_request.frappe,
				"get_cached_doc",
				return_value=frappe._dict(enabled=1),
			),
			patch.object(ch_exception_request.frappe, "new_doc") as new_doc,
			self.assertRaises(frappe.ValidationError),
		):
			ch_exception_request.create_from_delivery_note(
				dn.name,
				"Price Override",
				payload,
				requested_reason="Release test",
			)
		new_doc.assert_not_called()

	def test_sensitive_functions_do_not_bypass_permissions_or_commit(self):
		for function in (
			ch_scheme_receivable.record_settlement,
			ch_scheme_receivable.write_off,
			ch_exception_request.create_from_delivery_note,
		):
			source = inspect.getsource(function)
			self.assertNotIn("ignore_permissions", source)
			self.assertNotIn("frappe.db.commit", source)
