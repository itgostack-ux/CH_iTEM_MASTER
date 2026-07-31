import base64
from unittest import TestCase
from unittest.mock import patch

import frappe

from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
	CHWarrantyClaim,
)
from ch_item_master.ch_item_master.warranty_api import initiate_warranty_claim


class TestWarrantyClaimAPIE2E(TestCase):
	def test_zero_value_claim_runs_from_evidence_intake_to_audited_closure(self):
		"""Exercise the real submitted document and its governed lifecycle actions."""
		previous_user = frappe.session.user
		frappe.set_user("Administrator")
		files = []
		claim_name = None
		try:
			company = (
				frappe.db.get_single_value("Global Defaults", "default_company")
				or frappe.db.get_value("Company", {}, "name")
			)
			customer = frappe.db.get_value("Customer", {}, "name")
			item_code = frappe.db.get_value(
				"Item",
				{"disabled": 0, "is_stock_item": 1},
				"name",
			)
			issue_category = frappe.db.get_value("Issue Category", {}, "name")
			if not all((company, customer, item_code, issue_category)):
				self.skipTest("ERPNext warranty-claim baseline masters are unavailable")

			token = frappe.generate_hash(length=10)
			one_pixel_png = base64.b64decode(
				"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
				"/x8AAusB9Y9Z4L8AAAAASUVORK5CYII="
			)
			for index in range(4):
				file_doc = frappe.get_doc({
					"doctype": "File",
					"file_name": f"claim-e2e-{token}-{index}.png",
					"is_private": 1,
					"content": one_pixel_png + bytes([index]),
				})
				file_doc.insert(ignore_permissions=True)
				files.append(file_doc)

			result = initiate_warranty_claim(
				serial_no=f"CLAIM-E2E-{token}",
				customer=customer,
				item_code=item_code,
				company=company,
				issue_description="End-to-end governed zero-value repair claim",
				issue_categories=[issue_category],
				reported_at_company=company,
				estimated_repair_cost=0,
				mode_of_service="Walk-in",
				evidence_files=[
					{
						"file_name": file_doc.name,
						"file_url": file_doc.file_url,
					}
					for file_doc in files
				],
			)

			claim_name = result["claim_name"]
			claim = frappe.get_doc("CH Warranty Claim", claim_name)
			self.assertEqual(claim.docstatus, 1)
			self.assertEqual(claim.claim_status, "Approved")
			self.assertIn(issue_category, claim._issue_category_names())
			for file_doc, fieldname in zip(files, (
				"device_image_front",
				"device_image_back",
				"device_image_left",
				"device_image_right",
			)):
				file_doc.reload()
				self.assertEqual(file_doc.attached_to_doctype, "CH Warranty Claim")
				self.assertEqual(file_doc.attached_to_name, claim.name)
				self.assertEqual(file_doc.attached_to_field, fieldname)

			claim.mark_device_received(
				condition_on_receipt="Good",
				imei_verified=1,
				receiving_remarks="IMEI and condition verified",
			)
			claim.reload()
			self.assertEqual(claim.claim_status, "Device Received")

			claim.perform_intake_qc("Passed", "Intake checks passed")
			claim.reload()
			self.assertEqual(claim.claim_status, "QC Passed")

			claim.generate_processing_fee(fee_amount=0)
			claim.reload()
			self.assertEqual(claim.processing_fee_status, "Not Required")
			self.assertEqual(claim.claim_status, "Fee Paid")

			def create_ticket_stub(document, from_submit=False):
				document.db_set({
					"claim_status": "Ticket Created",
					"repair_status": "In Progress",
				})

			with patch.object(
				CHWarrantyClaim,
				"_create_gofix_ticket",
				autospec=True,
				side_effect=create_ticket_stub,
			):
				claim.create_repair_ticket("Gate-checked repair creation")
			claim.reload()
			self.assertEqual(claim.claim_status, "Ticket Created")

			claim.mark_repair_complete("Repair completed")
			claim.reload()
			self.assertEqual(claim.claim_status, "Final QC Pending")
			self.assertEqual(claim.final_qc_status, "Pending")

			claim.perform_final_qc("Passed", "Final checks passed")
			claim.reload()
			self.assertEqual(claim.claim_status, "Ready for Delivery")
			self.assertEqual(claim.settlement_status, "Settled")

			claim.mark_out_for_delivery(pickup_partner="Customer pickup")
			delivery_otp = claim.flags.delivery_otp_plaintext
			self.assertRegex(delivery_otp, r"^\d{6}$")
			claim.reload()
			self.assertEqual(claim.claim_status, "Out for Delivery")

			claim.mark_delivered_back(delivery_otp, "Handed to customer")
			claim.reload()
			self.assertEqual(claim.claim_status, "Delivered")

			claim.close_claim("Customer accepted repaired device")
			claim.reload()
			self.assertEqual(claim.claim_status, "Closed")
			self.assertEqual(claim.settlement_status, "Settled")
			self.assertGreaterEqual(len(claim.claim_log or []), 8)
		finally:
			if claim_name and frappe.db.exists("CH Warranty Claim", claim_name):
				claim = frappe.get_doc("CH Warranty Claim", claim_name)
				if claim.docstatus == 1:
					claim.cancel()
				frappe.delete_doc(
					"CH Warranty Claim",
					claim_name,
					ignore_permissions=True,
					force=True,
				)
			for file_doc in files:
				if frappe.db.exists("File", file_doc.name):
					frappe.delete_doc("File", file_doc.name, ignore_permissions=True)
			frappe.set_user(previous_user)
