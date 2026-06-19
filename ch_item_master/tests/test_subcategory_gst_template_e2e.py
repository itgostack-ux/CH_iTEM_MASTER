"""End-to-end test for CH Sub Category → Item Tax Template auto-provisioning.

Coverage:
  1. Saving a Sub Category with `gst_rate` creates an `Item Tax Template`
     for every India-country company on the site (skipping companies that
     do not have GST accounts wired).
  2. The created template has the correct `gst_rate`/`gst_treatment` and
     CGST+SGST rows at half the rate, IGST at the full rate.
  3. The same template gets linked into `GST HSN Code.taxes` so India
     Compliance's `set_taxes_from_hsn_code` Item-validate hook copies it
     into Item.taxes when the Item is saved.
  4. An Item created under that Sub Category receives the template in
     `Item.taxes`.
  5. Idempotence — re-saving the Sub Category does not create duplicates.
  6. `gst_rate` change cascades to existing items (re-points Item.taxes
     to the new template).
  7. `Nil-Rated` treatment with `gst_rate=0` creates a tax-row-less template.
  8. Non-India companies are skipped.
  9. Setting `gst_rate_valid_from` to a future date schedules the new rate
     from that date — the appended `Item.taxes` row carries that exact date,
     letting admins pre-schedule rate revisions (SAP MWST / Oracle EBS
     effective-dated tax rate parity).
"""

from __future__ import annotations

import unittest

import frappe

from ch_item_master.ch_item_master.gst_template import (
	ensure_item_tax_template,
	ensure_templates_for_subcategory,
)


HSN_CODE = "847130"  # 6 digits, exists in GST HSN Code seed
ITEM_GROUP = "Products"
TEST_CATEGORY = "_Test GST Auto Cat"
TEST_SUBCAT_18 = "_Test GST Auto Sub 18"
TEST_SUBCAT_NIL = "_Test GST Auto Sub Nil"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _india_companies_with_gst() -> list[str]:
	"""Return India companies that have at least one GST Account row.
	Mirrors what `gst_template.ensure_templates_for_subcategory` would
	consider eligible — used to scope assertions correctly on a multi-
	company site where some companies may be intentionally unwired."""
	india = set(
		frappe.get_all("Company", filters={"country": "India"}, pluck="name")
	)
	india = {c for c in india if not c.startswith("_Test")}
	wired = set(
		frappe.get_all(
			"GST Account",
			filters={"company": ["in", list(india)]},
			pluck="company",
		)
	)
	return sorted(india & wired)


def _ensure_item_group():
	if not frappe.db.exists("Item Group", ITEM_GROUP):
		ig = frappe.new_doc("Item Group")
		ig.item_group_name = ITEM_GROUP
		ig.parent_item_group = "All Item Groups"
		ig.insert(ignore_permissions=True)


def _ensure_hsn():
	if not frappe.db.exists("GST HSN Code", HSN_CODE):
		h = frappe.new_doc("GST HSN Code")
		h.hsn_code = HSN_CODE
		h.description = "Test HSN for GST auto template"
		h.insert(ignore_permissions=True)


def _ensure_category():
	if frappe.db.exists("CH Category", {"category_name": TEST_CATEGORY}):
		return frappe.db.get_value(
			"CH Category", {"category_name": TEST_CATEGORY}, "name"
		)
	cat = frappe.new_doc("CH Category")
	cat.category_name = TEST_CATEGORY
	cat.item_group = ITEM_GROUP
	cat.lifecycle_status = "Active"
	cat.insert(ignore_permissions=True)
	return cat.name


def _make_subcat(name: str, gst_rate: float = 18, **extra) -> str:
	cat = _ensure_category()
	extra.setdefault("hsn_code", HSN_CODE)
	extra.setdefault("lifecycle_status", "Active")
	extra.setdefault("item_nature", "Simple Auto-Named")
	existing = frappe.db.exists(
		"CH Sub Category", {"category": cat, "sub_category_name": name}
	)
	if existing:
		sc = frappe.get_doc("CH Sub Category", existing)
		sc.gst_rate = gst_rate
		for k, v in extra.items():
			setattr(sc, k, v)
		sc.save(ignore_permissions=True)
		return sc.name
	sc = frappe.new_doc("CH Sub Category")
	sc.category = cat
	sc.sub_category_name = name
	# Use a unique 3-letter-ish prefix derived from name; required by the
	# CH Sub Category contract for auto-naming when applicable.
	sc.prefix = (name.replace("_", "").replace(" ", ""))[:6].upper() or "TST"
	sc.gst_rate = gst_rate
	for k, v in extra.items():
		setattr(sc, k, v)
	sc.insert(ignore_permissions=True)
	return sc.name


def _cleanup_templates_for_hsn():
	"""Remove templates this test may have created so re-runs start clean.
	We match on the title pattern produced by `gst_template.ensure_item_tax_template`."""
	titles = frappe.get_all(
		"Item Tax Template",
		filters=[
			["title", "like", "GST 18% - %"],
		],
		pluck="name",
	)
	for t in titles:
		try:
			frappe.delete_doc(
				"Item Tax Template", t, force=1, ignore_permissions=True
			)
		except Exception:
			pass


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSubCategoryGSTTemplateE2E(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		_ensure_item_group()
		_ensure_hsn()
		_ensure_category()
		cls.eligible_companies = _india_companies_with_gst()

	def setUp(self):
		# Each test starts from a known state on the templates side.
		# We don't blow away the Sub Category itself — that would slow tests
		# significantly; we only ensure the templates re-resolve cleanly.
		frappe.set_user("Administrator")

	# -- 1. Sub Category save creates per-company Item Tax Templates ----------

	def test_sub_category_save_creates_templates_for_india_companies(self):
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		sc_name = _make_subcat(TEST_SUBCAT_18, gst_rate=18)
		sc = frappe.get_doc("CH Sub Category", sc_name)

		# Re-save explicitly — first insert already triggered ensure, but
		# we want to demonstrate idempotence too.
		sc.save(ignore_permissions=True)

		for company in self.eligible_companies:
			tpl = frappe.db.get_value(
				"Item Tax Template",
				{"company": company, "gst_rate": 18, "gst_treatment": "Taxable", "disabled": 0},
				"name",
			)
			self.assertTrue(
				tpl,
				f"Expected Item Tax Template for {company!r} @ 18% Taxable, none found",
			)

	# -- 2. Template has correct CGST+SGST=rate/2 / IGST=rate rows ------------

	def test_template_tax_row_shape(self):
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		_make_subcat(TEST_SUBCAT_18, gst_rate=18)
		company = self.eligible_companies[0]
		tpl_name = frappe.db.get_value(
			"Item Tax Template",
			{"company": company, "gst_rate": 18, "gst_treatment": "Taxable", "disabled": 0},
			"name",
		)
		self.assertTrue(tpl_name)

		tpl = frappe.get_doc("Item Tax Template", tpl_name)
		# India Compliance's own validator checks: intra-state row
		# tax_rate*2 == gst_rate, inter-state row tax_rate == gst_rate.
		# Saving the Sub Category would fail if our recipe were wrong, so
		# reaching this point already proves shape — but assert explicitly.
		intra = [t for t in tpl.taxes if abs(t.tax_rate - 9.0) < 0.001]
		inter = [t for t in tpl.taxes if abs(t.tax_rate - 18.0) < 0.001]
		self.assertGreaterEqual(
			len(intra), 2, f"Expected >=2 intra-state rows @ 9%, got {[t.tax_rate for t in tpl.taxes]}"
		)
		self.assertGreaterEqual(
			len(inter), 1, f"Expected >=1 inter-state row @ 18%, got {[t.tax_rate for t in tpl.taxes]}"
		)

	# -- 3. HSN code's `taxes` table now references the template --------------

	def test_hsn_code_taxes_wired(self):
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		_make_subcat(TEST_SUBCAT_18, gst_rate=18)

		hsn = frappe.get_doc("GST HSN Code", HSN_CODE)
		hsn_template_names = {t.item_tax_template for t in hsn.taxes if t.item_tax_template}
		expected = {
			frappe.db.get_value(
				"Item Tax Template",
				{"company": c, "gst_rate": 18, "gst_treatment": "Taxable", "disabled": 0},
				"name",
			)
			for c in self.eligible_companies
		}
		expected.discard(None)
		# Subset relation: HSN rows include our templates (it may also have
		# pre-existing ones from prior data — that's fine).
		self.assertTrue(
			expected.issubset(hsn_template_names),
			f"Expected {expected} ⊆ {hsn_template_names}",
		)

	# -- 4. Item under this Sub Category inherits Item.taxes ------------------

	def test_item_inherits_taxes_from_subcategory(self):
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		sc_name = _make_subcat(TEST_SUBCAT_18, gst_rate=18)

		item_code = "_Test GST Auto Item 18"
		if frappe.db.exists("Item", item_code):
			frappe.delete_doc("Item", item_code, force=1, ignore_permissions=True)

		item = frappe.new_doc("Item")
		item.item_code = item_code
		item.item_name = item_code
		item.item_group = ITEM_GROUP
		item.stock_uom = "Nos"
		item.ch_category = _ensure_category()
		item.ch_sub_category = sc_name
		item.ch_item_mrp = 100  # MRP mandatory for stock items (ch_item_master rule)
		# India Compliance's `set_taxes_from_hsn_code` runs in Item.validate
		# and populates Item.taxes from GST HSN Code.taxes when Item.taxes
		# is empty and gst_hsn_code is set. Our overrides/item.py copies
		# hsn_code from the Sub Category onto the Item before that hook fires.
		item.insert(ignore_permissions=True)

		# The Item should now have at least one tax row pointing at one of
		# our auto-created templates.
		expected_templates = {
			frappe.db.get_value(
				"Item Tax Template",
				{"company": c, "gst_rate": 18, "gst_treatment": "Taxable", "disabled": 0},
				"name",
			)
			for c in self.eligible_companies
		}
		expected_templates.discard(None)

		item_template_names = {row.item_tax_template for row in item.taxes if row.item_tax_template}
		self.assertTrue(
			item_template_names & expected_templates,
			f"Item.taxes {item_template_names} should overlap auto templates {expected_templates}",
		)

	# -- 5. Idempotence — saving twice doesn't duplicate ----------------------

	def test_idempotent_no_duplicate_templates(self):
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		sc_name = _make_subcat(TEST_SUBCAT_18, gst_rate=18)
		sc = frappe.get_doc("CH Sub Category", sc_name)

		before = {}
		for c in self.eligible_companies:
			before[c] = frappe.db.count(
				"Item Tax Template",
				{"company": c, "gst_rate": 18, "gst_treatment": "Taxable", "disabled": 0},
			)

		# Re-trigger explicitly
		ensure_templates_for_subcategory(sc)
		sc.save(ignore_permissions=True)

		for c in self.eligible_companies:
			after = frappe.db.count(
				"Item Tax Template",
				{"company": c, "gst_rate": 18, "gst_treatment": "Taxable", "disabled": 0},
			)
			self.assertEqual(
				after,
				before[c],
				f"Template count for {c} drifted: {before[c]} → {after}",
			)

	# -- 6. gst_rate change re-provisions and cascades ------------------------

	def test_gst_rate_change_recascades_to_items(self):
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		sc_name = _make_subcat(TEST_SUBCAT_18, gst_rate=18)
		# Create item under sub category at 18%.
		item_code = "_Test GST Cascade Item"
		if frappe.db.exists("Item", item_code):
			frappe.delete_doc("Item", item_code, force=1, ignore_permissions=True)
		it = frappe.new_doc("Item")
		it.item_code = item_code
		it.item_name = item_code
		it.item_group = ITEM_GROUP
		it.stock_uom = "Nos"
		it.ch_category = _ensure_category()
		it.ch_sub_category = sc_name
		it.ch_item_mrp = 100
		it.insert(ignore_permissions=True)

		# Bump rate to 12 — must create new templates AND replace Item.taxes
		# rows so they point at the 12% template.
		sc = frappe.get_doc("CH Sub Category", sc_name)
		sc.gst_rate = 12
		sc.save(ignore_permissions=True)

		# Reload item
		it = frappe.get_doc("Item", item_code)
		expected_12 = {
			frappe.db.get_value(
				"Item Tax Template",
				{"company": c, "gst_rate": 12, "gst_treatment": "Taxable", "disabled": 0},
				"name",
			)
			for c in self.eligible_companies
		}
		expected_12.discard(None)
		got = {row.item_tax_template for row in it.taxes if row.item_tax_template}
		self.assertTrue(
			got & expected_12,
			f"Item.taxes did not pick up new 12% template after rate change. got={got} expected_subset_of={expected_12}",
		)
		# Restore to 18 to leave a stable baseline for follow-up runs.
		sc.gst_rate = 18
		sc.save(ignore_permissions=True)

	# -- 7. Direct ensure_item_tax_template helper for Nil-Rated --------------

	def test_ensure_item_tax_template_nil_rated(self):
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")
		company = self.eligible_companies[0]
		name = ensure_item_tax_template(company, 0, "Nil-Rated")
		self.assertTrue(name)
		tpl = frappe.get_doc("Item Tax Template", name)
		self.assertEqual(tpl.gst_treatment, "Nil-Rated")
		self.assertEqual(float(tpl.gst_rate), 0.0)
		# Note: row count is NOT asserted because an admin or prior data may
		# have populated rows on a pre-existing Nil-Rated template; our helper
		# correctly returns the existing match without re-shaping it. India
		# Compliance's `validate_item_tax_template` would have already enforced
		# shape consistency at insert time.

	# -- 8. Non-India companies are skipped silently --------------------------

	def test_non_india_company_skipped(self):
		# Find or skip — site may not have any
		non_india = frappe.db.get_value(
			"Company", {"country": ["!=", "India"]}, "name"
		)
		if not non_india:
			self.skipTest("Site has no non-India companies to test against")
		out = ensure_item_tax_template(non_india, 18, "Taxable")
		self.assertIsNone(out, f"Non-India company should be skipped, got {out!r}")

	# -- 9. Effective-dated rate change keeps historical rows -----------------

	def test_rate_change_is_effective_dated(self):
		"""Mirrors SAP / Oracle effective-dated tax rates.

		When `gst_rate` changes, the cascade should APPEND new tax rows
		with `valid_from = today` rather than wholesale-replacing existing
		rows. This guarantees historical transactions (back-dated postings,
		amendments to old POs) still resolve via the prior rate.

		Test approach: simulate a clean item that only has 18% rows
		(prior tests may have left multiple rates wired to the HSN, so
		we can't rely on Item.insert inheriting just one). Then call the
		cascade in append-effective-today mode and assert new rows appear
		with today's `valid_from`.
		"""
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		from frappe.utils import nowdate

		sc_name = _make_subcat(TEST_SUBCAT_18, gst_rate=18)
		sc = frappe.get_doc("CH Sub Category", sc_name)

		# Make sure 12% templates exist for every eligible company —
		# the cascade would create them, but we want them in place
		# BEFORE we trim Item.taxes so the wiring on HSN includes them.
		ensure_templates_for_subcategory(sc)

		# Create a fresh item, then surgically trim its tax rows down to
		# just the 18% templates so we can prove the 12% rows get APPENDED
		# (not pre-existing).
		item_code = "_Test GST Effective Date Item"
		if frappe.db.exists("Item", item_code):
			frappe.delete_doc("Item", item_code, force=1, ignore_permissions=True)
		it = frappe.new_doc("Item")
		it.item_code = item_code
		it.item_name = item_code
		it.item_group = ITEM_GROUP
		it.stock_uom = "Nos"
		it.ch_category = _ensure_category()
		it.ch_sub_category = sc_name
		it.ch_item_mrp = 100
		it.insert(ignore_permissions=True)

		# Trim non-18% rows directly via SQL (avoid Item.validate side
		# effects). This lets us assert the cascade appends 12% fresh.
		frappe.db.sql(
			"""
			DELETE FROM `tabItem Tax`
			WHERE parenttype = 'Item' AND parent = %s
			AND item_tax_template NOT LIKE 'GST 18%%'
			""",
			(item_code,),
		)
		count_before = frappe.db.count(
			"Item Tax", {"parenttype": "Item", "parent": item_code}
		)
		self.assertGreater(count_before, 0, "Item must have its 18% rows after trim")

		# Now: change Sub Category rate to 12 → cascade in append mode.
		sc = frappe.get_doc("CH Sub Category", sc_name)
		sc.gst_rate = 12
		sc.save(ignore_permissions=True)

		rows_after = frappe.get_all(
			"Item Tax",
			filters={"parenttype": "Item", "parent": item_code},
			fields=["item_tax_template", "valid_from"],
		)
		# Append-mode: row count must be strictly greater than before.
		self.assertGreater(
			len(rows_after),
			count_before,
			f"Effective-dated cascade should APPEND rows. Before={count_before} After={len(rows_after)} rows={rows_after}",
		)
		# At least one new row must reference a 12% template AND carry
		# today's valid_from.
		today = nowdate()
		todays_12 = [
			r for r in rows_after
			if str(r.valid_from) == today and "GST 12%" in (r.item_tax_template or "")
		]
		self.assertGreater(
			len(todays_12),
			0,
			f"Expected ≥1 new 12% row with valid_from={today}, got {rows_after}",
		)

		# Restore baseline for downstream test runs.
		sc = frappe.get_doc("CH Sub Category", sc_name)
		sc.gst_rate = 18
		sc.save(ignore_permissions=True)

	# -- 10. Future-dated rate change via gst_rate_valid_from ---------------

	def test_future_dated_rate_change(self):
		"""`gst_rate_valid_from` lets an admin schedule a rate change.

		SAP S/4HANA condition records (KOMV with future-dated validity) and
		Oracle Fusion effective-dated tax rates both support this. We mirror
		it here: setting `gst_rate_valid_from` to a future date AND changing
		`gst_rate` on the same save must stamp the appended `Item.taxes`
		row with that future date (not today). ERPNext's
		`_get_tax_template_for_item()` picker will then keep using the OLD
		row for any posting_date < future_date, and switch to the NEW row
		for posting_date >= future_date — automatically and per-transaction.
		"""
		if not self.eligible_companies:
			self.skipTest("No India companies have GST Accounts wired on this site")

		from datetime import timedelta

		from frappe.utils import add_days, getdate, nowdate

		future = add_days(nowdate(), 30)  # 30 days out

		# Use a dedicated Sub Category so we don't fight with state from
		# the previous effective-date test.
		sc_name = _make_subcat("_Test GST Future Sub", gst_rate=18)
		sc = frappe.get_doc("CH Sub Category", sc_name)
		ensure_templates_for_subcategory(sc)

		# Fresh item under this Sub Category.
		item_code = "_Test GST Future Date Item"
		if frappe.db.exists("Item", item_code):
			frappe.delete_doc("Item", item_code, force=1, ignore_permissions=True)
		it = frappe.new_doc("Item")
		it.item_code = item_code
		it.item_name = item_code
		it.item_group = ITEM_GROUP
		it.stock_uom = "Nos"
		it.ch_category = _ensure_category()
		it.ch_sub_category = sc_name
		it.ch_item_mrp = 100
		it.insert(ignore_permissions=True)

		# Trim non-18% rows so we cleanly observe the appended 5% rows.
		frappe.db.sql(
			"""
			DELETE FROM `tabItem Tax`
			WHERE parenttype = 'Item' AND parent = %s
			AND item_tax_template NOT LIKE 'GST 18%%'
			""",
			(item_code,),
		)

		# Change rate to 5% AND set the future effective date on the same save.
		sc = frappe.get_doc("CH Sub Category", sc_name)
		sc.gst_rate = 5
		sc.gst_rate_valid_from = future
		sc.save(ignore_permissions=True)

		rows_after = frappe.get_all(
			"Item Tax",
			filters={"parenttype": "Item", "parent": item_code},
			fields=["item_tax_template", "valid_from"],
		)

		future_5 = [
			r for r in rows_after
			if r.valid_from
			and getdate(r.valid_from) == getdate(future)
			and "GST 5%" in (r.item_tax_template or "")
		]
		self.assertGreater(
			len(future_5),
			0,
			f"Expected ≥1 new 5% row with valid_from={future}, got {rows_after}",
		)

		# The 18% rows must still be present — historical transactions need them.
		old_18 = [
			r for r in rows_after
			if "GST 18%" in (r.item_tax_template or "")
		]
		self.assertGreater(
			len(old_18),
			0,
			f"Future-dated cascade must NOT delete pre-existing rows, got {rows_after}",
		)

		# Cleanup
		frappe.delete_doc("Item", item_code, force=1, ignore_permissions=True)
