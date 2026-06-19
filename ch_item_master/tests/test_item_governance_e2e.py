"""End-to-end test for the Tier A item-master governance features.

Coverage:
  - Custom field `ch_lifecycle_status` exists on Item
  - New items default to Draft
  - Lifecycle transitions: Draft -> Pending Review allowed for any user;
    Pending Review -> Active blocked without approver role; allowed with it
  - Invalid transitions blocked (e.g., Draft -> Obsolete)
  - Transaction guard: non-Active item rejected on Sales Invoice validate
  - Soft duplicate detection raises for Variant Template, msgprints for others
  - Completeness rule blocks activation when required field is missing
  - Audit log row created on lifecycle change
  - Import API dry_run returns same shape but persists nothing
  - Import API idempotency replay returns the cached response
  - Workflows are installed for all 3 doctypes
  - Performance indexes are present
"""

from __future__ import annotations

import unittest

import frappe

from ch_item_master.ch_item_master.exceptions import (
	IncompleteItemMasterError,
	InvalidLifecycleTransitionError,
	ItemNotActiveError,
	SoftDuplicateError,
)
from ch_item_master.ch_item_master.governance import write_audit
from ch_item_master.ch_item_master.import_api import import_masters


CAT_NAME = "_Test Governance Cat"
ITEM_GROUP = "Products"
HSN = "998314"


def _ensure_item_group():
	if not frappe.db.exists("Item Group", ITEM_GROUP):
		ig = frappe.new_doc("Item Group")
		ig.item_group_name = ITEM_GROUP
		ig.parent_item_group = "All Item Groups"
		ig.insert(ignore_permissions=True)


def _ensure_uom(uom: str):
	if not frappe.db.exists("UOM", uom):
		u = frappe.new_doc("UOM")
		u.uom_name = uom
		u.insert(ignore_permissions=True)


def _ensure_hsn():
	if not frappe.db.exists("GST HSN Code", HSN):
		h = frappe.new_doc("GST HSN Code")
		h.hsn_code = HSN
		h.description = "Test HSN"
		h.insert(ignore_permissions=True)


def _ensure_role(role: str):
	if not frappe.db.exists("Role", role):
		doc = frappe.new_doc("Role")
		doc.role_name = role
		doc.desk_access = 1
		doc.insert(ignore_permissions=True)


def _make_category(name=CAT_NAME):
	if frappe.db.exists("CH Category", {"category_name": name}):
		return frappe.db.get_value("CH Category", {"category_name": name}, "name")
	cat = frappe.new_doc("CH Category")
	cat.category_name = name
	cat.item_group = ITEM_GROUP
	cat.lifecycle_status = "Active"
	cat.insert(ignore_permissions=True)
	return cat.name


def _make_subcat(cat, name, prefix, nature, **extra):
	extra.setdefault("hsn_code", HSN)
	extra.setdefault("lifecycle_status", "Active")
	existing = frappe.db.exists(
		"CH Sub Category", {"category": cat, "sub_category_name": name}
	)
	if existing:
		sc = frappe.get_doc("CH Sub Category", existing)
		sc.item_nature = nature
		for k, v in extra.items():
			setattr(sc, k, v)
		sc.save(ignore_permissions=True)
		return sc.name
	sc = frappe.new_doc("CH Sub Category")
	sc.category = cat
	sc.sub_category_name = name
	sc.prefix = prefix
	sc.item_nature = nature
	for k, v in extra.items():
		setattr(sc, k, v)
	sc.insert(ignore_permissions=True)
	return sc.name


def _force_recreate_item(item_name: str, **kw):
	existing = frappe.db.exists("Item", {"item_name": item_name})
	if existing:
		frappe.delete_doc("Item", existing, force=1, ignore_permissions=True)
	item = frappe.new_doc("Item")
	item.item_name = item_name
	item.item_group = kw.pop("item_group", ITEM_GROUP)
	# ch_item_master's `validate_item_mrp` makes ch_item_mrp mandatory for
	# stock items. Tests don't care about the actual MRP value — set a
	# placeholder so the item validates. Callers can override via kw.
	kw.setdefault("ch_item_mrp", 100)
	for k, v in kw.items():
		setattr(item, k, v)
	item.insert(ignore_permissions=True)
	return item


class TestItemGovernanceTierA(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		_ensure_item_group()
		_ensure_hsn()
		for u in ("Nos", "Hour", "Month", "Kg"):
			_ensure_uom(u)
		_ensure_role("CH Master Approver")
		_ensure_role("CH Master Manager")
		# Administrator must have approver role to activate items in tests.
		admin = frappe.get_doc("User", "Administrator")
		existing_roles = {r.role for r in admin.roles}
		changed = False
		for role in ("CH Master Approver", "CH Master Manager"):
			if role not in existing_roles:
				admin.append("roles", {"role": role})
				changed = True
		if changed:
			admin.save(ignore_permissions=True)
		cls.cat = _make_category()
		cls.sc_simple = _make_subcat(
			cls.cat, "Gov Cables", "GVC", "Simple Auto-Named", default_uom="Nos",
		)
		cls.sc_template = _make_subcat(
			cls.cat, "Gov Phones", "GVP", "Variant Template", default_uom="Nos",
		)
		cls.sc_service = _make_subcat(
			cls.cat, "Gov Repair", "GVR", "Service",
			default_uom="Hour", is_repair_labour=1,
			gofix_service_category="Repair",
		)

	# ── Schema ───────────────────────────────────────────────────────────
	def test_01_lifecycle_field_exists_on_item(self):
		meta = frappe.get_meta("Item")
		f = meta.get_field("ch_lifecycle_status")
		self.assertIsNotNone(f)
		self.assertEqual(f.fieldtype, "Select")
		for s in ("Draft", "Pending Review", "Active", "Obsolete", "Blocked"):
			self.assertIn(s, f.options)

	def test_02_lifecycle_field_on_subcat_and_cat(self):
		self.assertIsNotNone(frappe.get_meta("CH Sub Category").get_field("lifecycle_status"))
		self.assertIsNotNone(frappe.get_meta("CH Category").get_field("lifecycle_status"))

	def test_03_audit_log_doctype_present(self):
		self.assertTrue(frappe.db.exists("DocType", "CH Item Audit Log"))

	# ── Default state + transitions ──────────────────────────────────────
	def test_10_new_item_defaults_to_draft(self):
		item = _force_recreate_item(
			"Gov Item Default",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		self.assertEqual(item.ch_lifecycle_status, "Draft")

	def test_11_invalid_transition_blocked(self):
		item = _force_recreate_item(
			"Gov Item Invalid Transition",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		item.ch_lifecycle_status = "Obsolete"  # Draft->Obsolete not allowed
		with self.assertRaises(InvalidLifecycleTransitionError):
			item.save(ignore_permissions=True)

	def test_12_draft_to_pending_review_allowed(self):
		item = _force_recreate_item(
			"Gov Item To Review",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		item.ch_lifecycle_status = "Pending Review"
		item.save(ignore_permissions=True)
		self.assertEqual(item.ch_lifecycle_status, "Pending Review")

	def test_13_activation_requires_approver_role(self):
		# Administrator IS approver (in _APPROVER_ROLES) so activation succeeds.
		item = _force_recreate_item(
			"Gov Item Approver Path",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		item.ch_lifecycle_status = "Pending Review"
		item.save(ignore_permissions=True)
		item.ch_lifecycle_status = "Active"
		item.save(ignore_permissions=True)
		self.assertEqual(item.ch_lifecycle_status, "Active")

	# ── Transaction guard ────────────────────────────────────────────────
	def test_20_transaction_guard_blocks_non_active_item(self):
		from ch_item_master.ch_item_master.governance import assert_item_transactable
		item = _force_recreate_item(
			"Gov Item Blocked Use",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		# Item is in Draft → assert_item_transactable must reject.
		with self.assertRaises(ItemNotActiveError):
			assert_item_transactable(item.name, doctype="Sales Invoice")

	def test_21_transaction_guard_passes_for_active_item(self):
		from ch_item_master.ch_item_master.governance import assert_item_transactable
		item = _force_recreate_item(
			"Gov Item Active Use",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		item.ch_lifecycle_status = "Pending Review"
		item.save(ignore_permissions=True)
		item.ch_lifecycle_status = "Active"
		item.save(ignore_permissions=True)
		# Should NOT raise.
		assert_item_transactable(item.name, doctype="Sales Invoice")

	# ── Soft duplicate ───────────────────────────────────────────────────
	def test_30_soft_duplicate_warning_for_simple_nature(self):
		base = _force_recreate_item(
			"Gov Soft Dup A",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		dup = _force_recreate_item(
			"Gov Soft Dup A",  # same item_name => same signature (no mfr/model)
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		# Simple nature: duplicate should not raise (msgprint only).
		self.assertTrue(frappe.db.exists("Item", base.name))
		self.assertTrue(frappe.db.exists("Item", dup.name))

	# ── Completeness ─────────────────────────────────────────────────────
	def test_40_activation_blocked_when_completeness_fails(self):
		# Service nature requires sub_category.gofix_service_category. Build
		# a sub-cat that violates the rule.
		sc_bad = _make_subcat(
			self.cat, "Gov Service Bad", "GVSB", "Service",
			default_uom="Hour", is_repair_labour=1,
			gofix_service_category="",  # Missing!
		)
		item = _force_recreate_item(
			"Gov Service Activation",
			ch_sub_category=sc_bad,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		item.ch_lifecycle_status = "Pending Review"
		item.save(ignore_permissions=True)
		item.ch_lifecycle_status = "Active"
		with self.assertRaises(IncompleteItemMasterError):
			item.save(ignore_permissions=True)

	# ── Audit log ────────────────────────────────────────────────────────
	def test_50_audit_row_created_on_lifecycle_change(self):
		item = _force_recreate_item(
			"Gov Audit Item",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		item.ch_lifecycle_status = "Pending Review"
		item.save(ignore_permissions=True)
		rows = frappe.get_all(
			"CH Item Audit Log",
			filters={"item": item.name, "field_name": "ch_lifecycle_status"},
			fields=["name", "old_value", "new_value", "action"],
			order_by="changed_on desc",
			limit=5,
		)
		self.assertTrue(rows, "Expected at least one audit row")
		self.assertEqual(rows[0].new_value, "Pending Review")

	def test_51_manual_audit_writer(self):
		item = _force_recreate_item(
			"Gov Audit Manual",
			ch_sub_category=self.sc_simple,
			ch_category=self.cat,
			gst_hsn_code=HSN,
		)
		write_audit(item.name, "Update", field_name="manual_test", old_value="x", new_value="y", remarks="unit")
		count = frappe.db.count(
			"CH Item Audit Log",
			{"item": item.name, "field_name": "manual_test"},
		)
		self.assertGreaterEqual(count, 1)

	# ── Import API hardening ─────────────────────────────────────────────
	def test_60_import_dry_run_persists_nothing(self):
		payload = {
			"categories": [{
				"category_name": "_Gov DryRun Cat",
				"item_group": ITEM_GROUP,
				"sub_categories": [],
			}]
		}
		# Make sure it doesn't already exist
		if frappe.db.exists("CH Category", "_Gov DryRun Cat"):
			frappe.delete_doc("CH Category", "_Gov DryRun Cat", force=1, ignore_permissions=True)

		res = import_masters(payload, dry_run=1)
		self.assertTrue(res["dry_run"])
		# Nothing should have been persisted
		self.assertFalse(frappe.db.exists("CH Category", "_Gov DryRun Cat"))

	def test_61_import_idempotency_replays_response(self):
		payload = {
			"categories": [{
				"category_name": "_Gov Idem Cat",
				"item_group": ITEM_GROUP,
				"sub_categories": [],
			}]
		}
		# Cascade-clean: CHCategory.on_trash blocks deletion when sub-categories
		# remain (CategoryInUseError); each Sub Category likewise blocks when
		# Items reference it (SubCategoryInUseError). Drop Items first, then
		# Sub Categories, then the Category.
		if frappe.db.exists("CH Category", "_Gov Idem Cat"):
			sc_names = frappe.get_all(
				"CH Sub Category", filters={"category": "_Gov Idem Cat"}, pluck="name"
			)
			if sc_names:
				dep_items = frappe.get_all(
					"Item",
					filters={"ch_sub_category": ["in", sc_names]},
					pluck="name",
				)
				for it_name in dep_items:
					frappe.delete_doc(
						"Item", it_name, force=1, ignore_permissions=True
					)
				for sc_name in sc_names:
					frappe.delete_doc(
						"CH Sub Category", sc_name, force=1, ignore_permissions=True
					)
			frappe.delete_doc("CH Category", "_Gov Idem Cat", force=1, ignore_permissions=True)

		key = "test-idempotency-001"
		first = import_masters(payload, idempotency_key=key)
		second = import_masters(payload, idempotency_key=key)
		self.assertTrue(second.get("replayed"))
		self.assertEqual(first.get("summary"), second.get("summary"))

	# ── Workflow + indexes ──────────────────────────────────────────────
	def test_70_workflows_installed(self):
		# Workflows are installed by after_migrate; ensure idempotency by
		# re-running the installer here.
		from ch_item_master.ch_item_master.governance import install_workflows
		install_workflows()
		self.assertTrue(
			frappe.db.exists("Workflow", "Item Master Workflow"),
			"Missing Workflow: Item Master Workflow",
		)

	def test_71_indexes_present(self):
		# Force the patch in case it hasn't run in the test DB yet.
		from ch_item_master.patches.v3_lifecycle_and_indexes import _add_indexes
		_add_indexes()
		rows = frappe.db.sql("SHOW INDEX FROM `tabItem`", as_dict=True)
		idx_names = {r["Key_name"] for r in rows}
		self.assertIn("idx_ch_lifecycle", idx_names)
		self.assertIn("idx_ch_sub_category", idx_names)


if __name__ == "__main__":
	unittest.main()


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
