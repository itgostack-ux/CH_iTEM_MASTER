import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from ch_item_master.ch_item_master.doctype.ch_exception_request.ch_exception_request import (
	CHExceptionRequest,
)
from ch_item_master.ch_item_master.doctype.ch_item_offer.ch_item_offer import CHItemOffer
from ch_item_master.ch_item_master.doctype.ch_item_price.ch_item_price import CHItemPrice
from ch_item_master.ch_item_master.doctype.ch_price_upload_batch.ch_price_upload_batch import (
	CHPriceUploadBatch,
)
from ch_item_master.ch_item_master.doctype.ch_vendor_info_record.ch_vendor_info_record import (
	CHVendorInfoRecord,
)
from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
	CHWarrantyClaim,
	_ANNIVERSARY_ELIGIBLE_PLAN_TYPES,
	_VAS_ELIGIBLE_PLAN_TYPES,
)
from ch_item_master.ch_item_master import warranty_api
from ch_item_master.seed_status_registry import CROSS_APP_MAPPINGS
from ch_item_master.supplier_scheme.doctype.scheme_product_map.scheme_product_map import (
	SchemeProductMap,
)


class FakeDocument:
	def __init__(self, values, before=None, is_new=False):
		self._values = frappe._dict(values)
		self._before = frappe._dict(before) if before is not None else None
		self._is_new = is_new
		self.flags = frappe._dict()
		self.meta = frappe._dict(get_label=lambda fieldname: fieldname)
		for key, value in self._values.items():
			setattr(self, key, value)

	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def is_new(self):
		return self._is_new

	def get_doc_before_save(self):
		return self._before


class TestApprovalCrudGuards(TestCase):
	def test_item_offer_rejects_direct_approval(self):
		before = {fieldname: None for fieldname in CHItemOffer._PROTECTED_FIELDS}
		before.update({"approval_status": "Pending Approval", "status": "Draft"})
		doc = FakeDocument({**before, "approval_status": "Approved", "status": "Active"}, before)
		doc._PROTECTED_FIELDS = CHItemOffer._PROTECTED_FIELDS
		doc._APPROVAL_SENSITIVE_FIELDS = CHItemOffer._APPROVAL_SENSITIVE_FIELDS
		doc._has_approval_context = lambda: False
		with self.assertRaises(frappe.PermissionError):
			CHItemOffer._validate_approval_transition(doc)

	def test_item_price_rejects_direct_activation(self):
		before = {fieldname: None for fieldname in CHItemPrice._PROTECTED_FIELDS}
		before["status"] = "Draft"
		doc = FakeDocument({**before, "status": "Active"}, before)
		doc._PROTECTED_FIELDS = CHItemPrice._PROTECTED_FIELDS
		doc._APPROVAL_SENSITIVE_FIELDS = CHItemPrice._APPROVAL_SENSITIVE_FIELDS
		doc._has_approval_context = lambda: False
		with self.assertRaises(frappe.PermissionError):
			CHItemPrice._validate_approval_transition(doc)

	def test_vendor_record_rejects_direct_approval(self):
		before = {fieldname: None for fieldname in CHVendorInfoRecord._PROTECTED_FIELDS}
		before["approval_status"] = "Submitted"
		doc = FakeDocument({**before, "approval_status": "Approved"}, before)
		doc._PROTECTED_FIELDS = CHVendorInfoRecord._PROTECTED_FIELDS
		doc.APPROVAL_SENSITIVE_FIELDS = CHVendorInfoRecord.APPROVAL_SENSITIVE_FIELDS
		doc._has_approval_context = lambda: False
		with self.assertRaises(frappe.PermissionError):
			CHVendorInfoRecord._validate_approval_transition(doc)

	def test_price_batch_rejects_forged_initial_state(self):
		doc = FakeDocument({"status": "Approved", "items": [], "category_approvals": []}, is_new=True)
		doc._PROTECTED_FIELDS = CHPriceUploadBatch._PROTECTED_FIELDS
		doc._has_approval_context = lambda: False
		with self.assertRaises(frappe.PermissionError):
			CHPriceUploadBatch._validate_approval_transition(doc)

	def test_exception_rejects_caller_approval_evidence(self):
		values = {fieldname: None for fieldname in CHExceptionRequest._PROTECTED_FIELDS}
		values.update({"status": "Approved", "approver": "forged@example.com"})
		doc = FakeDocument(values, is_new=True)
		doc._PROTECTED_FIELDS = CHExceptionRequest._PROTECTED_FIELDS
		doc._has_approval_context = lambda: False
		with self.assertRaises(frappe.PermissionError):
			CHExceptionRequest._validate_approval_transition(doc)

	def test_warranty_rejects_plaintext_otp_on_insert(self):
		values = {fieldname: None for fieldname in CHWarrantyClaim._GOVERNANCE_FIELDS}
		values.update({"claim_status": "Draft", "delivery_otp": "123456"})
		doc = FakeDocument(values, is_new=True)
		doc._GOVERNANCE_FIELDS = CHWarrantyClaim._GOVERNANCE_FIELDS
		with self.assertRaises(frappe.PermissionError):
			CHWarrantyClaim._validate_governance_fields(doc)

	def test_warranty_governance_accepts_database_blank_normalization(self):
		before = {fieldname: None for fieldname in CHWarrantyClaim._GOVERNANCE_FIELDS}
		values = {
			fieldname: (0 if fieldname in CHWarrantyClaim._GOVERNANCE_NUMERIC_FIELDS else "")
			for fieldname in CHWarrantyClaim._GOVERNANCE_FIELDS
		}
		doc = FakeDocument(values, before=before)
		doc._GOVERNANCE_FIELDS = CHWarrantyClaim._GOVERNANCE_FIELDS
		doc._GOVERNANCE_NUMERIC_FIELDS = CHWarrantyClaim._GOVERNANCE_NUMERIC_FIELDS

		CHWarrantyClaim._validate_governance_fields(doc)

	def test_product_map_rejects_caller_verification(self):
		doc = FakeDocument(
			{"mapping_source": "Verified", "verified_by": "forged@example.com", "verified_on": None},
			is_new=True,
		)
		doc._has_verification_context = lambda: False
		with self.assertRaises(frappe.PermissionError):
			SchemeProductMap._validate_verification_evidence(doc)

	def test_warranty_otp_digest_fields_are_privileged(self):
		path = Path(__file__).parents[1] / "ch_item_master/doctype/ch_warranty_claim/ch_warranty_claim.json"
		definition = json.loads(path.read_text())
		fields = {field["fieldname"]: field for field in definition["fields"]}
		for fieldname in (
			"delivery_otp",
			"delivery_otp_sent_at",
			"delivery_otp_attempts",
			"delivery_otp_consumed_at",
		):
			self.assertEqual(fields[fieldname].get("permlevel"), 1)
			self.assertEqual(fields[fieldname].get("read_only"), 1)
		self.assertTrue(any(
			permission.get("role") == "System Manager"
			and permission.get("permlevel") == 1
			and permission.get("read")
			for permission in definition["permissions"]
		))

	def test_warranty_new_document_defaults_are_governable(self):
		doc = frappe.new_doc("CH Warranty Claim")
		self.assertIn(doc.approval_status, (None, ""))
		self.assertEqual(doc.settlement_status, "Pending")
		doc._validate_governance_fields()

	def test_warranty_schema_starts_approval_status_blank(self):
		path = Path(__file__).parents[1] / "ch_item_master/doctype/ch_warranty_claim/ch_warranty_claim.json"
		definition = json.loads(path.read_text())
		fields = {field["fieldname"]: field for field in definition["fields"]}
		self.assertTrue(fields["approval_status"]["options"].startswith("\n"))
		self.assertEqual(fields["settlement_status"].get("default"), "Pending")

	def test_all_configured_warranty_plan_types_are_classifiable(self):
		path = Path(__file__).parents[1] / "ch_item_master/doctype/ch_warranty_plan/ch_warranty_plan.json"
		definition = json.loads(path.read_text())
		plan_type = next(
			field for field in definition["fields"] if field["fieldname"] == "plan_type"
		)
		configured = {value for value in plan_type["options"].splitlines() if value}

		self.assertTrue(
			configured.issubset(
				set(_ANNIVERSARY_ELIGIBLE_PLAN_TYPES) | set(_VAS_ELIGIBLE_PLAN_TYPES)
			)
		)

	def test_warranty_governance_covers_qc_and_settlement(self):
		for fieldname in (
			"final_qc_status",
			"final_qc_by",
			"final_qc_at",
			"final_qc_remarks",
			"gogizmo_invoice",
			"gogizmo_payment_ref",
			"customer_invoice",
			"customer_payment_ref",
			"settlement_status",
		):
			self.assertIn(fieldname, CHWarrantyClaim._GOVERNANCE_FIELDS)
		self.assertNotIn("final_qc_result", CHWarrantyClaim._GOVERNANCE_FIELDS)

	def test_warranty_submission_requires_four_distinct_images(self):
		values = {
			fieldname: None
			for fieldname in (
				"device_image_front",
				"device_image_back",
				"device_image_left",
				"device_image_right",
				"device_image_top",
				"device_image_bottom",
			)
		}
		values.update({
			"device_image_front": "/private/files/front.jpg",
			"device_image_back": "/private/files/back.jpg",
			"device_image_left": "/private/files/left.jpg",
			"device_image_right": "/private/files/right.jpg",
			"claim_media": [],
		})
		doc = FakeDocument(values)
		with patch(
			"ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim.get_int_setting",
			return_value=4,
		):
			CHWarrantyClaim._validate_submission_evidence(doc)
			doc.device_image_right = doc.device_image_left
			with self.assertRaises(frappe.ValidationError):
				CHWarrantyClaim._validate_submission_evidence(doc)

	def test_submitted_warranty_intake_images_are_immutable(self):
		before = frappe._dict(
			docstatus=1,
			device_image_front="/private/files/original.png",
		)
		doc = FakeDocument(
			{"device_image_front": "/private/files/replacement.png"},
			before=before,
		)
		with self.assertRaises(frappe.PermissionError):
			CHWarrantyClaim._validate_intake_evidence_immutability(doc)

	def test_pos_claim_action_wrappers_exist(self):
		for method_name in (
			"request_additional_approval_claim",
			"resolve_additional_approval_claim",
			"perform_final_qc_claim",
			"settle_claim_finance",
			"close_warranty_claim",
		):
			self.assertTrue(callable(getattr(warranty_api, method_name, None)), method_name)

	def test_claim_issue_categories_are_normalized_before_insert(self):
		with patch.object(warranty_api, "get_int_setting", return_value=3):
			self.assertEqual(
				warranty_api._normalize_claim_issue_categories(
					'[{"issue_category": "Battery"}, "Screen & Display", "Battery"]',
					"Camera",
				),
				["Battery", "Screen & Display", "Camera"],
			)
			with self.assertRaises(frappe.ValidationError):
				warranty_api._normalize_claim_issue_categories([], None)

	def test_gofix_completion_cannot_bypass_claim_fulfilment_gates(self):
		mapping = CROSS_APP_MAPPINGS["SR_TO_CLAIM_STATUS"]
		for service_status in ("Completed", "Invoiced", "Delivered"):
			self.assertEqual(mapping[service_status], "Final QC Pending")
