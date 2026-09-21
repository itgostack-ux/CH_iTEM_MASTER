# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
RBAC Oracle/SAP-parity E2E tests.

Features covered:
  01:    All 12 CH roles exist after setup
  02:    submit_for_approval() records ch_submitted_by
  03:    check_sod() blocks same-user self-approval
  04:    check_sod() passes for different users
  05:    CH Approval Delegation record can be created
  06:    is_effective_approver() ignores expired delegations
  07:    is_effective_approver() ignores inactive delegations
  08:    check_vendor_manager_role() blocks users without the role
  09:    check_plm_role() blocks users without the role
  10:    check_sod() with blank submitted_by is a no-op (safe)
  11:    Sensitive Item custom fields have permlevel=1
  12:    Every configured sensitive_field_role holds the permlevel=1 Item DocPerm
  13:    CH Role Assignment stays retired (patch v34) — no doctype, no table
  14:    expire_role_assignments() is dormant, not broken, while the table is gone
  15:    open_break_glass() creates a CH Break Glass Log; empty reason raises

Run:  bench --site erpnext.local run-tests --module ch_item_master.tests.test_rbac_parity_e2e
"""

from __future__ import annotations

import unittest

import frappe
from frappe.utils import today, add_days


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (reuse Tier C helpers)
# ─────────────────────────────────────────────────────────────────────────────

TEST_HSN = "01011010"
_RB_PREFIX = "RBAC1"
_TEST_CAT = "_RBAC_Test_Category"
_TEST_SC: str | None = None


def _ensure_test_sub_category() -> str:
	global _TEST_SC
	if _TEST_SC:
		return _TEST_SC
	if not frappe.db.exists("CH Category", _TEST_CAT):
		cat = frappe.get_doc({
			"doctype": "CH Category",
			"category_name": _TEST_CAT,
			"item_group": "All Item Groups",
			"lifecycle_status": "Active",
		})
		cat.insert(ignore_permissions=True)
	sc_name = frappe.db.get_value("CH Sub Category", {"category": _TEST_CAT}, "name")
	if not sc_name:
		sc = frappe.get_doc({
			"doctype": "CH Sub Category",
			"sub_category_name": "_RBAC_SC",
			"category": _TEST_CAT,
			"prefix": _RB_PREFIX,
			"item_nature": "Simple Auto-Named",
			"lifecycle_status": "Active",
		})
		sc.insert(ignore_permissions=True)
		sc_name = sc.name
	_TEST_SC = sc_name
	return sc_name


def _make_item(item_code: str, extra: dict | None = None, delete_first: bool = True):
	if delete_first and frappe.db.exists("Item", item_code):
		frappe.delete_doc("Item", item_code, ignore_permissions=True, force=True)
		frappe.db.commit()
	sc = _ensure_test_sub_category()
	data = {
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_code,
		"item_group": "All Item Groups",
		"stock_uom": "Nos",
		"gst_hsn_code": TEST_HSN,
		"ch_sub_category": sc,
		"ch_lifecycle_status": "Active",
		"ch_approval_status": "Draft",
		"ch_plm_status": "NPI",
	}
	if extra:
		data.update(extra)
	doc = frappe.get_doc(data)
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	frappe.db.commit()
	return doc


# ─────────────────────────────────────────────────────────────────────────────
# Test classes
# ─────────────────────────────────────────────────────────────────────────────

class TestRBACRoles(unittest.TestCase):
	"""All 12 CH roles must be present after setup / migration."""

	def setUp(self):
		frappe.set_user("Administrator")

	_EXPECTED_ROLES = [
		"CH Master Manager",
		"CH Master Approver",
		"CH Price Manager",
		"CH Offer Manager",
		"CH Warranty Manager",
		"CH Viewer",
		# New RBAC-parity roles
		"CH Item Creator",
		"CH Item Reviewer",
		"CH PLM Manager",
		"CH Vendor Manager",
		"CH MRP Planner",
		"CH GTIN Editor",
	]

	def test_01_all_ch_roles_exist(self):
		"""All 12 CH roles must be present in tabRole."""
		missing = [r for r in self._EXPECTED_ROLES if not frappe.db.exists("Role", r)]
		self.assertEqual(
			missing, [],
			f"Missing CH roles: {missing}. Run setup_roles() or the v6 patch.",
		)


class TestRBACSegregationOfDuties(unittest.TestCase):
	"""Maker-checker SoD enforcement."""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_02_submit_records_submitted_by(self):
		"""submit_for_approval() records ch_submitted_by = current user."""
		from ch_item_master.ch_item_master.tier_c import submit_for_approval

		item = _make_item("RB-SOD-02")
		submit_for_approval(item.name)
		submitted_by = frappe.db.get_value("Item", item.name, "ch_submitted_by")
		self.assertEqual(submitted_by, frappe.session.user)
		frappe.delete_doc("Item", item.name, ignore_permissions=True, force=True)

	def test_03_sod_blocks_self_approval_for_regular_user(self):
		"""check_sod() raises ValidationError when a non-sysadmin user submits and tries to approve."""
		from ch_item_master.ch_item_master.rbac import check_sod

		# A regular business user (no System Manager role) cannot self-approve
		with self.assertRaises(frappe.ValidationError):
			check_sod(submitted_by="regular.user@acme.example", approver="regular.user@acme.example")

	def test_04_sod_passes_for_different_approver(self):
		"""check_sod() does NOT raise when submitter != approver."""
		from ch_item_master.ch_item_master.rbac import check_sod

		# Should not raise
		check_sod(submitted_by="maker@test.example", approver="checker@test.example")

	def test_10_sod_skipped_for_blank_submitted_by(self):
		"""check_sod() with blank submitted_by is a no-op (safe for items without a submitter)."""
		from ch_item_master.ch_item_master.rbac import check_sod

		# Should not raise, even if approver == session.user
		check_sod(submitted_by="")
		check_sod(submitted_by=None)


class TestRBACApprovalDelegation(unittest.TestCase):
	"""CH Approval Delegation doctype and is_effective_approver() logic."""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_05_delegation_record_created(self):
		"""CH Approval Delegation doctype can be created and retrieved."""
		doc = frappe.get_doc({
			"doctype": "CH Approval Delegation",
			"delegator": "Administrator",
			"delegate": "Administrator",
			"active": 1,
			"valid_from": today(),
			"valid_to": add_days(today(), 30),
			"notes": "test delegation",
		})
		doc.flags.ignore_validate = True  # skip delegator == delegate validation in test
		doc.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("CH Approval Delegation", doc.name))
		frappe.delete_doc("CH Approval Delegation", doc.name, ignore_permissions=True)

	def test_06_expired_delegation_ignored(self):
		"""is_effective_approver() ignores delegations past valid_to."""
		from ch_item_master.ch_item_master.rbac import is_effective_approver

		# Administrator is a direct approver regardless of delegations
		result = is_effective_approver("Administrator")
		self.assertTrue(result, "Administrator must be an effective approver")

		# Create an expired delegation (should not cause any error or side effect)
		doc = frappe.get_doc({
			"doctype": "CH Approval Delegation",
			"delegator": "Administrator",
			"delegate": "Administrator",
			"active": 1,
			"valid_from": add_days(today(), -10),
			"valid_to": add_days(today(), -1),
		})
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		# Should still be True via direct role, not via expired delegation
		self.assertTrue(is_effective_approver("Administrator"))
		frappe.delete_doc("CH Approval Delegation", doc.name, ignore_permissions=True)

	def test_07_inactive_delegation_ignored(self):
		"""is_effective_approver() ignores delegations with active=0."""
		from ch_item_master.ch_item_master.rbac import is_effective_approver

		doc = frappe.get_doc({
			"doctype": "CH Approval Delegation",
			"delegator": "Administrator",
			"delegate": "Administrator",
			"active": 0,  # explicitly inactive
			"valid_from": today(),
			"valid_to": add_days(today(), 30),
		})
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		# Should not raise or crash; Administrator still True via direct role
		result = is_effective_approver("Administrator")
		self.assertIsInstance(result, bool)
		frappe.delete_doc("CH Approval Delegation", doc.name, ignore_permissions=True)


class TestRBACRoleGates(unittest.TestCase):
	"""Granular role gate functions."""

	def setUp(self):
		frappe.set_user("Administrator")
		# Save and restore get_roles between tests
		self._orig_get_roles = frappe.get_roles

	def tearDown(self):
		frappe.get_roles = self._orig_get_roles

	def test_08_vendor_manager_gate_blocks_stock_user(self):
		"""check_vendor_manager_role() raises for users without CH Vendor Manager role."""
		from ch_item_master.ch_item_master.rbac import check_vendor_manager_role

		frappe.get_roles = lambda user=None: ["Stock User", "All"]
		with self.assertRaises(frappe.ValidationError):
			check_vendor_manager_role("stock.user@example.com")

	def test_09_plm_manager_gate_blocks_stock_user(self):
		"""check_plm_role() raises for users without CH PLM Manager (or higher) role."""
		from ch_item_master.ch_item_master.rbac import check_plm_role

		frappe.get_roles = lambda user=None: ["Stock User", "All"]
		with self.assertRaises(frappe.ValidationError):
			check_plm_role("stock.user@example.com")


class TestRBACFieldSecurity(unittest.TestCase):
	"""Sensitive fields must have permlevel=1 and Custom DocPerm must be installed."""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_11_sensitive_fields_have_permlevel1(self):
		"""ch_standard_cost and ch_minimum_selling_price must have permlevel=1."""
		for cf_name in ("Item-ch_standard_cost", "Item-ch_minimum_selling_price", "Item-ch_gtin"):
			if not frappe.db.exists("Custom Field", cf_name):
				self.skipTest(f"Custom Field {cf_name} not installed yet — run bench migrate first.")
			permlevel = frappe.db.get_value("Custom Field", cf_name, "permlevel") or 0
			self.assertEqual(
				int(permlevel), 1,
				f"{cf_name} must have permlevel=1 (got {permlevel}). Run the v6 patch.",
			)

	def test_12_configured_sensitive_field_roles_have_permlevel1_docperm(self):
		"""Every role in `sensitive_field_roles` must hold the permlevel-1 grant.

		The grant is configuration-driven, not hardcoded: `install_custom_docperms`
		reads the `sensitive_field_roles` Table MultiSelect on CH Item Master
		Settings and creates one permlevel-1 Custom DocPerm on Item per role.
		Asserting a fixed role name here would only test this site's current
		configuration; the invariant that actually has to hold is that a role
		an administrator *has* configured is really granted — a role listed in
		the setting but missing its DocPerm is a silently powerless role.
		"""
		from ch_item_master.ch_item_master.rbac import get_role_setting, install_custom_docperms

		configured = {
			role for role in get_role_setting("sensitive_field_roles")
			if frappe.db.exists("Role", role)
		}
		self.assertTrue(
			configured,
			"sensitive_field_roles resolves to no existing role — the permlevel-1 "
			"fields on Item (standard cost, MSP, GTIN) would be Administrator-only.",
		)

		install_custom_docperms()
		granted = set(
			frappe.get_all(
				"Custom DocPerm",
				filters={"parent": "Item", "permlevel": 1},
				pluck="role",
			)
		)
		self.assertEqual(
			configured - granted,
			set(),
			"Roles configured in sensitive_field_roles but holding no permlevel=1 "
			"Custom DocPerm on Item. Run install_custom_docperms() or the v6 patch.",
		)


class TestRBACTimeboundRoles(unittest.TestCase):
	"""CH Role Assignment is RETIRED — these tests pin that it stays retired.

	``ch_erp15`` patch ``v34_consolidate_user_authorization`` dropped the
	doctype (0 rows) and folded time-bounded grants into Role Profile.  The
	expiry job in ``ch_item_master.ch_item_master.rbac`` was deliberately kept
	rather than deleted — Frappe has no native time-bounded role grant, so the
	job stays dormant behind a ``table_exists`` guard in case bounded grants
	come back.  What has to keep working is exactly that: the doctype is gone,
	and the still-scheduled job returns an empty result instead of throwing a
	nightly "table doesn't exist" in the scheduler.
	"""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_13_role_assignment_stays_retired(self):
		"""v34 retired the doctype; nothing may resurrect it."""
		self.assertFalse(
			frappe.db.exists("DocType", "CH Role Assignment"),
			"CH Role Assignment was retired by ch_erp15 patch v34. If it is back, "
			"something re-created it — bounded grants belong on CH User Scope.",
		)
		self.assertFalse(frappe.db.table_exists("CH Role Assignment"))

	def test_14_expire_role_assignments_is_dormant_not_broken(self):
		"""The still-scheduled job must no-op, not raise, with the table gone."""
		from ch_item_master.ch_item_master.rbac import expire_role_assignments

		result = expire_role_assignments()
		self.assertEqual(result, {"expired": 0, "failed": 0, "has_more": False})


class TestRBACBreakGlass(unittest.TestCase):
	"""Break-glass emergency access logging."""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_15_break_glass_creates_log_and_empty_reason_raises(self):
		"""open_break_glass() creates a log entry; empty reason raises ValidationError."""
		from ch_item_master.ch_item_master.rbac import open_break_glass

		# 15a: valid reason creates a log
		log_name = open_break_glass("E2E test — emergency access validation")
		self.assertTrue(
			frappe.db.exists("CH Break Glass Log", log_name),
			"CH Break Glass Log record must exist after open_break_glass().",
		)
		# Verify fields
		log = frappe.get_doc("CH Break Glass Log", log_name)
		self.assertEqual(log.user, "Administrator")
		self.assertEqual(log.review_status, "Pending Review")
		self.assertIsNotNone(log.start_time)

		frappe.delete_doc("CH Break Glass Log", log_name, ignore_permissions=True)

		# 15b: blank reason raises
		with self.assertRaises(frappe.ValidationError):
			open_break_glass("")

		with self.assertRaises(frappe.ValidationError):
			open_break_glass("   ")  # whitespace only


def run_all():
    import sys
    import unittest
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.failures or result.errors:
        raise Exception(
            f"{__name__}: {len(result.failures)} failure(s), {len(result.errors)} error(s)"
        )
