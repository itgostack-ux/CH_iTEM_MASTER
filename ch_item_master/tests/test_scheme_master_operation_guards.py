import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ch_item_master import async_dispatch
from ch_item_master.ch_core.doctype.ch_state import ch_state
from ch_item_master.ch_core.doctype.ch_store import ch_store
from ch_item_master.ch_item_master.doctype.ch_scheme_receivable import ch_scheme_receivable
from ch_item_master.supplier_scheme.doctype.scheme_document_upload import scheme_document_upload
from ch_item_master.supplier_scheme.doctype.scheme_product_map.scheme_product_map import (
	SchemeProductMap,
)


class TestSchemeMasterOperationGuards(TestCase):
	def test_sensitive_endpoints_are_post_only(self):
		for function in (
			ch_scheme_receivable.send_dunning_notice,
			ch_scheme_receivable.create_from_pos_invoice,
			scheme_document_upload.extract_scheme_data,
			scheme_document_upload.create_schemes_from_upload,
			ch_state.ensure_state_api,
			ch_store.create_pos_profile_for_store,
			SchemeProductMap.mark_verified,
		):
			self.assertEqual(
				frappe.allowed_http_methods_for_whitelisted_func[function],
				["POST"],
			)

	def test_invoice_doc_event_uses_private_handler(self):
		self.assertTrue(async_dispatch._SCHEME_RECEIVABLE_HOOK.endswith("._create_from_pos_invoice"))
		self.assertNotIn(
			ch_scheme_receivable._create_from_pos_invoice,
			frappe.allowed_http_methods_for_whitelisted_func,
		)

	def test_public_invoice_rebuild_checks_configured_role_before_read(self):
		with (
			patch.object(
				ch_scheme_receivable,
				"require_role_setting",
				side_effect=frappe.PermissionError,
			),
			patch.object(ch_scheme_receivable, "_create_from_pos_invoice") as create,
			self.assertRaises(frappe.PermissionError),
		):
			ch_scheme_receivable.create_from_pos_invoice("INV-TEST")
		create.assert_not_called()

	def test_dunning_authorizes_and_locks_before_state_read(self):
		doc = SimpleNamespace(doctype="CH Scheme Receivable", name="CHSR-TEST", docstatus=0)
		with (
			patch.object(ch_scheme_receivable.frappe, "get_doc", return_value=doc),
			patch.object(ch_scheme_receivable, "require_scoped_document_action") as authorize,
			self.assertRaises(frappe.ValidationError),
		):
			ch_scheme_receivable.send_dunning_notice(doc.name)
		authorize.assert_called_once_with(
			doc,
			"scheme_receivable_settlement_roles",
			ch_scheme_receivable._SETTLEMENT_ROLES,
			action="send a scheme receivable dunning notice",
			permission_types=("read", "write"),
			store_field="store",
			lock=True,
		)

	def test_dunning_resend_is_idempotent(self):
		doc = SimpleNamespace(
			doctype="CH Scheme Receivable",
			name="CHSR-TEST",
			docstatus=1,
			status="Claimed",
			outstanding_amount=100,
			due_date="2026-07-01",
			claim_date="2026-07-01",
			last_dunning_date="2026-07-22",
		)
		with (
			patch.object(ch_scheme_receivable.frappe, "get_doc", return_value=doc),
			patch.object(ch_scheme_receivable, "require_scoped_document_action"),
			patch.object(ch_scheme_receivable, "nowdate", return_value="2026-07-22"),
			patch.object(ch_scheme_receivable, "get_int_setting", return_value=7),
			patch.object(ch_scheme_receivable.frappe, "sendmail") as sendmail,
		):
			result = ch_scheme_receivable.send_dunning_notice(doc.name)
		self.assertTrue(result["already_sent"])
		sendmail.assert_not_called()

	def test_upload_denies_before_file_or_ai_access(self):
		doc = SimpleNamespace(name="SDU-TEST", doctype="Scheme Document Upload")
		with (
			patch.object(scheme_document_upload.frappe, "get_doc", return_value=doc),
			patch.object(
				scheme_document_upload,
				"_require_upload_action",
				side_effect=frappe.PermissionError,
			),
			patch.object(scheme_document_upload, "_get_owned_private_file") as get_file,
			patch.object(scheme_document_upload, "_get_ai_settings") as get_ai,
			self.assertRaises(frappe.PermissionError),
		):
			scheme_document_upload.extract_scheme_data(doc.name)
		get_file.assert_not_called()
		get_ai.assert_not_called()

	def test_product_map_verification_uses_scoped_lock_and_standard_save(self):
		doc = SchemeProductMap(
			{
				"doctype": "Scheme Product Map",
				"name": "SPM-TEST",
				"company": "Test Company",
			}
		)
		with (
			patch(
				"ch_item_master.supplier_scheme.doctype.scheme_product_map.scheme_product_map.require_scoped_document_action"
			) as authorize,
			patch.object(doc, "save") as save,
		):
			doc.mark_verified()
		authorize.assert_called_once()
		self.assertTrue(authorize.call_args.kwargs["lock"])
		self.assertEqual(authorize.call_args.kwargs["company_field"], "company")
		save.assert_called_once_with()

	def test_store_profile_denies_before_provisioning(self):
		doc = SimpleNamespace(doctype="CH Store", name="STORE-TEST")
		with (
			patch.object(ch_store.frappe, "get_doc", return_value=doc),
			patch.object(
				ch_store,
				"require_scoped_document_action",
				side_effect=frappe.PermissionError,
			),
			patch.object(ch_store, "_create_store_pos_profile_with_permissions") as provision,
			self.assertRaises(frappe.PermissionError),
		):
			ch_store.create_pos_profile_for_store(doc.name)
		provision.assert_not_called()

	def test_sensitive_paths_do_not_bypass_permissions_or_commit(self):
		for function in (
			ch_scheme_receivable._create_from_pos_invoice,
			ch_scheme_receivable.send_dunning_notice,
			scheme_document_upload.extract_scheme_data,
			scheme_document_upload.create_schemes_from_upload,
			scheme_document_upload._create_single_circular,
			SchemeProductMap.mark_verified,
			ch_state._ensure_state,
			ch_store._create_store_pos_profile_with_permissions,
		):
			source = inspect.getsource(function)
			self.assertNotIn("ignore_permissions", source)
			self.assertNotIn("frappe.db.commit", source)

	def test_company_anchors_are_present_in_scheme_doctypes(self):
		root = Path(__file__).resolve().parents[1]
		for relative in (
			"supplier_scheme/doctype/scheme_document_upload/scheme_document_upload.json",
			"supplier_scheme/doctype/supplier_scheme_circular/supplier_scheme_circular.json",
			"supplier_scheme/doctype/scheme_product_map/scheme_product_map.json",
		):
			data = json.loads((root / relative).read_text())
			fields = {field["fieldname"] for field in data["fields"]}
			self.assertIn("company", fields)
