# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Go-live blocker regression guards (2026-08-31 sweep).

Covers the four fixes shipped together:
  1. Item creation against a DISABLED CH Sub Category is refused
     (governance.validate_sub_category_enabled, wired on Item.validate).
  2. create_from_pos_invoice enforces the configured scheme-management role
     BEFORE any invoice read (proven here against a real non-privileged user;
     the mock-level ordering proof lives in test_scheme_master_operation_guards).
  3. CH Item Price is company-scoped: permission_query_conditions +
     has_permission per the ch_erp15.txn_scope precedent.
  4. Vendor info-record chain: an SoD-exempt effective approver's upsert is
     immediately sourceable, while a non-exempt vendor manager's record stays
     Draft (maker-checker preserved); vendor performance falls back to the
     caller's default company instead of dead-ending unrestricted users.

Every test rolls back — no committed fixtures.
"""

from unittest import TestCase
from unittest.mock import patch

import frappe

from ch_item_master import security
from ch_item_master.ch_item_master import governance, rbac, tier_c

_SCOPED_USER = "kevin@gmail.com"
_TEST_HSN = "01011010"


def _rollback_and_reset():
	frappe.set_user("Administrator")
	frappe.db.rollback()
	frappe.clear_messages()


def _make_disabled_sub_category(disabled=1):
	"""Insert a throwaway category + sub category inside the test transaction."""
	cat_name = "_GLB Guard Category"
	if not frappe.db.exists("CH Category", cat_name):
		frappe.get_doc({
			"doctype": "CH Category",
			"category_name": cat_name,
			"item_group": "All Item Groups",
			"lifecycle_status": "Active",
		}).insert(ignore_permissions=True)
	sc = frappe.get_doc({
		"doctype": "CH Sub Category",
		"sub_category_name": "_GLB Guard SC",
		"category": cat_name,
		"prefix": "GLBG",
		"item_nature": "Simple Auto-Named",
		"lifecycle_status": "Active",
		"disabled": disabled,
	})
	sc.insert(ignore_permissions=True)
	return sc


def _new_item_doc(item_code, sub_category):
	doc = frappe.get_doc({
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_code,
		"item_group": "All Item Groups",
		"stock_uom": "Nos",
		"gst_hsn_code": _TEST_HSN,
		"ch_category": frappe.db.get_value("CH Sub Category", sub_category, "category"),
		"ch_sub_category": sub_category,
		"ch_lifecycle_status": "Draft",
		"ch_approval_status": "Draft",
		"ch_plm_status": "NPI",
		"ch_item_mrp": 1000,
	})
	doc.flags.ignore_mandatory = True
	return doc


def _find_unmapped_vendor_pair():
	"""Return (item_code, supplier) with no CH Vendor Info Record yet."""
	suppliers = frappe.get_all("Supplier", pluck="name", limit_page_length=5)
	items = frappe.get_all("Item", pluck="name", limit_page_length=25)
	for supplier in suppliers:
		mapped = set(frappe.get_all(
			"CH Vendor Info Record",
			filters={"supplier": supplier, "item_code": ("in", items)},
			pluck="item_code"))
		for item_code in items:
			if item_code not in mapped:
				return item_code, supplier
	return None, None


class TestDisabledSubCategoryItemGate(TestCase):
	"""Fix 1: no new Items under a disabled CH Sub Category."""

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		_rollback_and_reset()

	def test_item_insert_refused_against_disabled_sub_category(self):
		sc = _make_disabled_sub_category(disabled=1)
		doc = _new_item_doc("GLB-DISABLED-SC-ITEM", sc.name)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.insert(ignore_permissions=True, ignore_mandatory=True)
		self.assertIn("disabled", str(ctx.exception).lower())

	def test_existing_item_still_saves_after_sub_category_disabled(self):
		# Governance must gate the ATTACHMENT only: an Item created while the
		# sub category was enabled must remain editable after it is disabled,
		# or the catalogue team could never obsolete its own leftovers.
		sc = _make_disabled_sub_category(disabled=0)
		item = _new_item_doc("GLB-ENABLED-SC-ITEM", sc.name)
		item.insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("CH Sub Category", sc.name, "disabled", 1)
		item.reload()
		item.description = "edited after sub category retirement"
		item.flags.ignore_mandatory = True
		item.save(ignore_permissions=True)  # must not raise

	def test_hook_unit_refuses_only_new_attachments(self):
		fake_new = frappe._dict(ch_sub_category="SC-X")
		fake_new.is_new = lambda: True
		with patch.object(governance.frappe.db, "get_value", return_value=1):
			with self.assertRaises(frappe.ValidationError):
				governance.validate_sub_category_enabled(fake_new)

		fake_existing = frappe._dict(ch_sub_category="SC-X")
		fake_existing.is_new = lambda: False
		fake_existing.has_value_changed = lambda fieldname: False
		with patch.object(governance.frappe.db, "get_value", return_value=1):
			governance.validate_sub_category_enabled(fake_existing)  # must not raise


class TestSchemeRebuildRoleGateBinds(TestCase):
	"""Fix 2: the configured-role policy binds for a real non-privileged user."""

	def tearDown(self):
		_rollback_and_reset()

	def test_scoped_user_is_refused_before_any_invoice_read(self):
		from ch_item_master.ch_item_master.doctype.ch_scheme_receivable import (
			ch_scheme_receivable,
		)

		frappe.set_user(_SCOPED_USER)
		with self.assertRaises(frappe.PermissionError):
			# The invoice name is deliberately nonexistent: the role gate must
			# refuse first, so no "invoice not found" detail can ever leak.
			ch_scheme_receivable.create_from_pos_invoice("GLB-NO-SUCH-INVOICE")


class TestCHItemPriceCompanyScope(TestCase):
	"""Fix 3: CH Item Price rows outside the user's company scope are hidden."""

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		_rollback_and_reset()

	def test_hooks_are_wired(self):
		self.assertIn(
			"ch_item_master.security.get_ch_item_price_query",
			frappe.get_hooks("permission_query_conditions").get("CH Item Price", []))
		self.assertIn(
			"ch_item_master.security.has_ch_item_price_permission",
			frappe.get_hooks("has_permission").get("CH Item Price", []))

	def test_query_builder_scopes_and_fails_closed(self):
		with patch.object(security, "get_user_allowed_companies", return_value=["Alpha Co"]):
			clause = security.get_ch_item_price_query("someone@example.com")
			self.assertIn("`tabCH Item Price`.`company` in", clause)
			self.assertIn("Alpha Co", clause)
		with patch.object(security, "get_user_allowed_companies", return_value=[]):
			self.assertEqual(security.get_ch_item_price_query("someone@example.com"), "1=0")
		with patch.object(security, "get_user_allowed_companies", return_value=None):
			self.assertIsNone(security.get_ch_item_price_query("someone@example.com"))

	def test_doc_level_check_denies_out_of_scope_company(self):
		with patch.object(security, "get_user_allowed_companies", return_value=["Alpha Co"]):
			self.assertFalse(security.has_ch_item_price_permission(
				doc=frappe._dict(company="Beta Co"), user="someone@example.com"))
			self.assertTrue(security.has_ch_item_price_permission(
				doc=frappe._dict(company="Alpha Co"), user="someone@example.com"))

	def test_scoped_user_sees_only_own_companies_live(self):
		allowed = security.get_user_allowed_companies(_SCOPED_USER)
		if allowed is None:
			self.skipTest(f"{_SCOPED_USER} is unrestricted on this site")
		out_of_scope = frappe.get_all(
			"CH Item Price",
			filters={"company": ("not in", allowed or [""])},
			fields=["name", "company"],
			limit_page_length=1)

		frappe.set_user(_SCOPED_USER)
		rows = frappe.get_list(
			"CH Item Price", fields=["name", "company"], limit_page_length=0)
		for row in rows:
			self.assertIn(
				row.company, allowed,
				f"CH Item Price {row.name} ({row.company}) leaked to {_SCOPED_USER}")

		if out_of_scope:
			leaked = frappe.get_doc("CH Item Price", out_of_scope[0].name)
			self.assertFalse(
				frappe.has_permission("CH Item Price", "read", doc=leaked, user=_SCOPED_USER),
				"doc-level read on an out-of-scope CH Item Price must be denied")


class TestVendorInfoChainGovernance(TestCase):
	"""Fix 4: the upsert → sourcing chain is live again, without gutting SoD."""

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		_rollback_and_reset()

	def test_exempt_approver_upsert_is_immediately_sourceable(self):
		item_code, supplier = _find_unmapped_vendor_pair()
		if not item_code:
			self.skipTest("no unmapped item+supplier pair available")
		name = tier_c.upsert_vendor_info(item_code, supplier, standard_price=123.0)
		self.assertEqual(
			frappe.db.get_value("CH Vendor Info Record", name, "approval_status"),
			"Approved",
			"a privileged, SoD-exempt author's record must land Approved")
		info = tier_c.get_vendor_info(item_code, supplier=supplier)
		self.assertIsNotNone(info, "the sourcing read path must serve the record")

	def test_non_exempt_manager_record_stays_draft(self):
		item_code, supplier = _find_unmapped_vendor_pair()
		if not item_code:
			self.skipTest("no unmapped item+supplier pair available")

		def roles_without_sod_exemption(fieldname, user=None):
			return fieldname != "break_glass_supervisor_roles"

		with patch.object(rbac, "_has_configured_role", side_effect=roles_without_sod_exemption):
			name = tier_c.upsert_vendor_info(item_code, supplier, standard_price=321.0)
			self.assertEqual(
				frappe.db.get_value("CH Vendor Info Record", name, "approval_status"),
				"Draft",
				"maker-checker must still hold for a non-exempt author")
			self.assertIsNone(
				tier_c.get_vendor_info(item_code, supplier=supplier),
				"a Draft record must stay invisible to sourcing until approved")

	def test_vendor_performance_defaults_company_for_unrestricted_caller(self):
		item_code, supplier = _find_unmapped_vendor_pair()
		if not item_code:
			self.skipTest("no unmapped item+supplier pair available")
		name = tier_c.record_vendor_performance(
			item_code, supplier, otif_pct=90.0, risk_level="Low")
		self.assertTrue(
			frappe.db.get_value("CH Vendor Performance", name, "company"),
			"an unrestricted caller must fall back to a default company")

	def test_approval_gate_follows_session_roles(self):
		doc = frappe._dict(
			name="GLB-GATE-ITEM",
			ch_lifecycle_status="Active",
			ch_approval_status="Draft")
		with patch.object(tier_c.frappe, "get_roles", return_value=["Stock User"]):
			with self.assertRaises(frappe.ValidationError):
				tier_c.enforce_approval_gate(doc)
		with patch.object(tier_c.frappe, "get_roles", return_value=["CH Master Approver"]):
			tier_c.enforce_approval_gate(doc)  # warns, must not raise
		frappe.clear_messages()
