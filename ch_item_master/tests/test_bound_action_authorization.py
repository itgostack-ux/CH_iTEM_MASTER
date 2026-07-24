import ast
import inspect
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ch_item_master import security
from ch_item_master.ch_item_master.doctype.ch_coupon_campaign import ch_coupon_campaign
from ch_item_master.ch_item_master.doctype.ch_coupon_campaign.ch_coupon_campaign import (
	CHCouponCampaign,
)
from ch_item_master.ch_item_master.doctype.ch_voucher import ch_voucher
from ch_item_master.ch_item_master.doctype.ch_voucher.ch_voucher import CHVoucher
from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
	CHWarrantyClaim,
)


class TestBoundActionAuthorization(TestCase):
	def _doc(self):
		doc = Mock()
		doc.doctype = "CH Warranty Claim"
		doc.name = "CH-WC-TEST"
		doc.get.side_effect = lambda field: {
			"name": doc.name,
			"company": "Test Company",
			"reported_at_store": "Test Store",
		}.get(field)
		return doc

	def test_read_only_user_cannot_run_sensitive_bound_action(self):
		doc = self._doc()
		with (
			patch.object(security, "is_privileged_user", return_value=False),
			patch.object(security, "require_role_setting"),
			patch.object(security.frappe.db, "get_value", return_value=doc.name),
			patch.object(security.frappe, "has_permission", return_value=False),
			self.assertRaises(frappe.PermissionError),
		):
			security.require_scoped_document_action(
				doc,
				"warranty_claim_approval_roles",
				("CH Warranty Manager",),
				lock=True,
				store_field="reported_at_store",
			)

	def test_configured_writer_is_scoped_and_row_locked(self):
		doc = self._doc()
		with (
			patch.object(security, "is_privileged_user", return_value=False),
			patch.object(security, "require_role_setting") as require_role,
			patch.object(security.frappe.db, "get_value", return_value=doc.name) as get_value,
			patch.object(security.frappe, "has_permission", return_value=True),
			patch.object(security, "ensure_company_access") as ensure_company,
			patch(
				"ch_erp15.ch_erp15.scope.assert_user_has_store_scope"
			) as ensure_store,
		):
			security.require_scoped_document_action(
				doc,
				"warranty_claim_approval_roles",
				("CH Warranty Manager",),
				lock=True,
				store_field="reported_at_store",
				user="approver@example.com",
			)

		require_role.assert_called_once()
		get_value.assert_any_call(
			"CH Warranty Claim", doc.name, "name", for_update=True
		)
		doc.reload.assert_called_once_with()
		ensure_company.assert_called_once_with("Test Company", user="approver@example.com")
		ensure_store.assert_called_once_with(
			store="Test Store",
			company="Test Company",
			user="approver@example.com",
			msg="You are not permitted to act on this company or store.",
		)

	def test_immutable_privileged_user_retains_access(self):
		doc = self._doc()
		with (
			patch.object(security, "is_privileged_user", return_value=True),
			patch.object(security, "require_role_setting") as require_role,
			patch.object(security.frappe.db, "get_value", return_value=doc.name),
			patch.object(security.frappe, "has_permission") as has_permission,
		):
			security.require_scoped_document_action(
				doc,
				"voucher_management_roles",
				("CH Price Manager",),
				lock=True,
				user="Administrator",
			)

		require_role.assert_not_called()
		has_permission.assert_not_called()

	def test_all_whitelisted_claim_mutators_use_central_action_gate(self):
		tree = ast.parse(inspect.getsource(CHWarrantyClaim))
		class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef))
		mutators = {
			"approve",
			"reject",
			"need_more_info",
			"request_additional_approval",
			"resolve_additional_approval",
			"perform_final_qc",
			"mark_repair_complete",
			"schedule_pickup",
			"mark_picked_up",
			"mark_out_for_delivery",
			"mark_delivered_back",
			"settle_claim",
			"close_claim",
			"mark_device_received",
			"perform_intake_qc",
			"generate_processing_fee",
			"send_fee_payment_link",
			"mark_fee_paid",
			"waive_processing_fee",
			"create_repair_ticket",
			"send_to_manufacturer",
		}
		methods = {node.name: node for node in class_node.body if isinstance(node, ast.FunctionDef)}
		self.assertTrue(mutators.issubset(methods))
		for name in mutators:
			calls = [
				node
				for node in ast.walk(methods[name])
				if isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "_require_action"
			]
			self.assertTrue(calls, f"{name} is missing the central action gate")

	def test_coupon_code_export_denies_before_reading_codes(self):
		campaign = CHCouponCampaign({
			"doctype": "CH Coupon Campaign",
			"name": "CPN-CAMP-TEST",
			"company": "Test Company",
			"docstatus": 1,
			"status": "Active",
		})
		with (
			patch.object(
				ch_coupon_campaign,
				"require_scoped_document_action",
				side_effect=frappe.PermissionError,
			),
			patch.object(ch_coupon_campaign.frappe, "get_all") as get_all,
			self.assertRaises(frappe.PermissionError),
		):
			campaign.export_codes()
		get_all.assert_not_called()

	def test_voucher_mutation_denies_before_state_change(self):
		voucher = CHVoucher({
			"doctype": "CH Voucher",
			"name": "CHVCH-TEST",
			"company": "Test Company",
			"status": "Draft",
			"docstatus": 0,
		})
		with (
			patch.object(
				ch_voucher,
				"require_scoped_document_action",
				side_effect=frappe.PermissionError,
			),
			patch.object(voucher, "submit") as submit,
			self.assertRaises(frappe.PermissionError),
		):
			voucher.activate()
		submit.assert_not_called()
