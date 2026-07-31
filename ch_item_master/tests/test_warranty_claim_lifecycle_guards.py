from unittest import TestCase
from unittest.mock import patch

import frappe

from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
	CHWarrantyClaim,
)


class ActionDocument:
	def __init__(self, **values):
		self.__dict__.update(values)
		self.doctype = "CH Warranty Claim"
		self.name = values.get("name", "TEST-CLAIM")
		self.flags = frappe._dict()
		self._logs = []
		self._require_action = lambda *args, **kwargs: None

	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def db_set(self, fieldname, value=None, **kwargs):
		updates = fieldname if isinstance(fieldname, dict) else {fieldname: value}
		for key, item in updates.items():
			setattr(self, key, item)

	def _log(self, action, from_status, to_status, remarks="", save=True):
		self._logs.append((action, from_status, to_status, remarks))


class TestWarrantyClaimLifecycleGuards(TestCase):
	def test_final_qc_pass_requires_no_payment_when_all_shares_are_zero(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Final QC Pending",
			gogizmo_share=0,
			customer_share=0,
			final_qc_status="Pending",
		)

		result = CHWarrantyClaim.perform_final_qc(doc, "Passed", "All checks passed")

		self.assertEqual(result["claim_status"], "Ready for Delivery")
		self.assertEqual(doc.final_qc_status, "Passed")
		self.assertEqual(doc.settlement_status, "Settled")

	def test_final_qc_failure_returns_device_to_repair(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Final QC Pending",
			gogizmo_share=100,
			customer_share=0,
			final_qc_status="Pending",
			repair_status="Completed",
		)

		result = CHWarrantyClaim.perform_final_qc(doc, "Failed", "Speaker still faulty")

		self.assertEqual(result["claim_status"], "In Repair")
		self.assertEqual(doc.final_qc_status, "Failed")
		self.assertEqual(doc.repair_status, "In Progress")

	def test_delivery_rejects_unsettled_claim(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Final QC Passed",
			final_qc_status="Passed",
			settlement_status="Pending",
			pickup_partner="",
			pickup_tracking_no="",
		)

		with self.assertRaises(frappe.ValidationError):
			CHWarrantyClaim.mark_out_for_delivery(doc)

	def test_delivery_accepts_ready_and_settled_claim(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Ready for Delivery",
			final_qc_status="Passed",
			settlement_status="Settled",
			pickup_partner="",
			pickup_tracking_no="",
		)
		doc._issue_delivery_otp = lambda: "123456"

		result = CHWarrantyClaim.mark_out_for_delivery(doc, pickup_partner="Self")

		self.assertEqual(result["claim_status"], "Out for Delivery")
		self.assertTrue(result["otp_issued"])
		self.assertEqual(doc.logistics_status, "Out for Delivery")

	def test_settlement_moves_to_payment_pending_without_payment_documents(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Final QC Passed",
			final_qc_status="Passed",
			gogizmo_share=100,
			customer_share=0,
			gogizmo_invoice="SINV-1",
			customer_invoice="",
			gogizmo_payment_ref="",
			customer_payment_ref="",
		)
		doc._validate_settlement_invoice = lambda *args, **kwargs: None
		doc._validate_settlement_payment = lambda reference, *args, **kwargs: bool(reference)

		result = CHWarrantyClaim.settle_claim(doc)

		self.assertEqual(result["settlement_status"], "Pending")
		self.assertEqual(result["claim_status"], "Payment Pending")

	def test_paid_pos_invoice_advances_processing_fee_without_duplicate_gl(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Fee Pending",
			processing_fee_status="Pending",
			processing_fee_invoice="",
			processing_fee_amount=250,
			company="Test Company",
			customer="TEST-CUSTOMER",
		)
		invoice = frappe._dict(
			name="SINV-TEST-1",
			docstatus=1,
			is_return=0,
			custom_warranty_claim=doc.name,
			company=doc.company,
			customer=doc.customer,
			rounded_total=250,
			grand_total=250,
			outstanding_amount=0,
			payments=[frappe._dict(mode_of_payment="UPI", amount=250)],
		)

		with patch(
			"ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim.frappe.get_doc",
			return_value=invoice,
		):
			result = CHWarrantyClaim.record_processing_fee_invoice(doc, invoice.name)

		self.assertEqual(result["claim_status"], "Fee Paid")
		self.assertEqual(doc.processing_fee_status, "Paid")
		self.assertEqual(doc.processing_fee_invoice, invoice.name)
		self.assertFalse(getattr(doc, "processing_fee_journal_entry", None))

	def test_unpaid_pos_invoice_cannot_settle_processing_fee(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Fee Pending",
			processing_fee_status="Pending",
			processing_fee_invoice="",
			processing_fee_amount=250,
			company="Test Company",
			customer="TEST-CUSTOMER",
		)
		invoice = frappe._dict(
			name="SINV-TEST-2",
			docstatus=1,
			is_return=0,
			custom_warranty_claim=doc.name,
			company=doc.company,
			customer=doc.customer,
			rounded_total=250,
			grand_total=250,
			outstanding_amount=250,
			payments=[],
		)

		with patch(
			"ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim.frappe.get_doc",
			return_value=invoice,
		):
			with self.assertRaises(frappe.ValidationError):
				CHWarrantyClaim.record_processing_fee_invoice(doc, invoice.name)

	def test_close_rejects_blank_settlement_for_delivered_claim(self):
		doc = ActionDocument(
			docstatus=1,
			claim_status="Delivered",
			settlement_status="",
			gogizmo_share=100,
			customer_share=0,
		)

		with self.assertRaises(frappe.ValidationError):
			CHWarrantyClaim.close_claim(doc)

	def test_multi_issue_company_share_uses_most_restrictive_override(self):
		doc = ActionDocument(
			warranty_plan="PLAN-1",
			issue_category="",
		)
		doc._issue_category_names = lambda: ["Screen & Display", "Water Damage"]
		plan = frappe._dict(coverage_rules=[
			frappe._dict(
				issue_type="Screen & Display",
				company_share_percent=80,
			),
			frappe._dict(
				issue_type="Water Damage",
				company_share_percent=50,
			),
		])

		with patch(
			"ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim.frappe.get_cached_doc",
			return_value=plan,
		):
			self.assertEqual(CHWarrantyClaim._get_company_share_percent(doc), 50)
