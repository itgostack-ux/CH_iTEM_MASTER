import json
from pathlib import Path
from unittest import TestCase

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
)
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
