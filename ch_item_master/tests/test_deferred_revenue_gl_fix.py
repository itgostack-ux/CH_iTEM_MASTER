"""Regression checks for Active VAS Plan deferred revenue JE account selection.

Run:
    bench --site erpnext.local execute ch_item_master.tests.test_deferred_revenue_gl_fix.run
"""

from __future__ import annotations

import frappe

from ch_item_master.ch_item_master.doctype.active_vas_plans.active_vas_plans import CHSoldPlan


class _DummyPlan:
	def __init__(self):
		self.name = "CH-SP-TEST-0001"
		self.company = "BestBuy Mobiles Pvt Ltd"
		self.plan_price = 2000
		self.sales_invoice = "ACC-SINV-TEST-0001"
		self.item_code = "PLAN-ITEM-001"
		self.start_date = "2026-06-09"
		self._db_set = {}

	def get(self, key):
		return getattr(self, key, None)

	def db_set(self, key, value):
		self._db_set[key] = value


class _FakeJE:
	def __init__(self):
		self.name = None
		self.company = None
		self.posting_date = None
		self.user_remark = None
		self.accounts = []
		self.flags = frappe._dict()

	def append(self, table, row):
		if table != "accounts":
			raise AssertionError(f"Unexpected child table: {table}")
		self.accounts.append(row)

	def insert(self):
		self.name = "ACC-JV-TEST-0001"
		return self

	def submit(self):
		return self


def run():
	orig_get_all = frappe.get_all
	orig_new_doc = frappe.new_doc
	orig_get_value = frappe.db.get_value
	orig_log_error = frappe.log_error

	try:
		captured = {}
		frappe.log_error = lambda *args, **kwargs: None

		def _fake_new_doc(doctype):
			if doctype != "Journal Entry":
				raise AssertionError(f"Unexpected doctype: {doctype}")
			je = _FakeJE()
			captured["je"] = je
			return je

		def _fake_get_all(doctype, filters=None, fields=None, order_by=None):
			if doctype != "Sales Invoice Item":
				return []
			return [
				{"idx": 1, "income_account": "Warranty Income - BM", "amount": 2000, "base_amount": 2000},
				{"idx": 2, "income_account": "Warranty Income - BM", "amount": 500, "base_amount": 500},
			]

		def _fake_get_value(doctype, filters, fieldname=None):
			if doctype == "Account":
				return "Deferred Revenue - BM"
			if doctype == "Company" and fieldname == "default_income_account":
				return "Sales - BM"
			if doctype == "Company" and fieldname == "default_receivable_account":
				return "Debtors - BM"
			return None

		frappe.new_doc = _fake_new_doc
		frappe.get_all = _fake_get_all
		frappe.db.get_value = _fake_get_value

		plan = _DummyPlan()
		CHSoldPlan._post_deferred_revenue_gl(plan)

		je = captured.get("je")
		if not je:
			raise AssertionError("Deferred revenue JE was not created")

		if not je.flags.get("ch_system_generated_je"):
			raise AssertionError("System-generated JE flag was not set")

		if len(je.accounts) != 2:
			raise AssertionError(f"Expected 2 JE lines, found {len(je.accounts)}")

		debit_line = je.accounts[0]
		credit_line = je.accounts[1]

		if debit_line.get("account") != "Warranty Income - BM":
			raise AssertionError(f"Debit should hit income account; got {debit_line.get('account')}")
		if debit_line.get("account") == "Debtors - BM":
			raise AssertionError("Debit incorrectly hit Debtors account")
		if credit_line.get("account") != "Deferred Revenue - BM":
			raise AssertionError(f"Credit should hit deferred revenue; got {credit_line.get('account')}")

		if plan._db_set.get("custom_deferred_revenue_je") != "ACC-JV-TEST-0001":
			raise AssertionError("Active VAS Plan did not store created deferred revenue JE reference")

		print("[PASS] Deferred revenue JE uses income->deferred accounts and sets trusted system flag")

		# Ambiguous income account must NOT block plan activation / POS sale.
		# It should log for accounts review and fall back to the company default
		# income account so the core transaction always completes.
		def _fake_get_all_ambiguous(doctype, filters=None, fields=None, order_by=None):
			if doctype != "Sales Invoice Item":
				return []
			return [
				{"idx": 1, "income_account": "Income A - BM", "amount": 1000, "base_amount": 1000},
				{"idx": 2, "income_account": "Income B - BM", "amount": 1200, "base_amount": 1200},
			]

		frappe.get_all = _fake_get_all_ambiguous
		plan2 = _DummyPlan()
		CHSoldPlan._post_deferred_revenue_gl(plan2)
		je2 = captured.get("je")
		if not je2 or len(je2.accounts) != 2:
			raise AssertionError("Ambiguous case did not post a fallback deferred revenue JE")
		if je2.accounts[0].get("account") != "Sales - BM":
			raise AssertionError(
				f"Ambiguous case should fall back to default income account; got {je2.accounts[0].get('account')}"
			)
		if je2.accounts[1].get("account") != "Deferred Revenue - BM":
			raise AssertionError("Ambiguous fallback credit should still hit deferred revenue")
		print("[PASS] Ambiguous SI income accounts fall back to default income account (non-blocking)")

		print("Deferred revenue GL regression: ALL PASS")
	finally:
		frappe.get_all = orig_get_all
		frappe.new_doc = orig_new_doc
		frappe.db.get_value = orig_get_value
		frappe.log_error = orig_log_error
