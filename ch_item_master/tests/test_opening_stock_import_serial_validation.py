import unittest

import frappe

from ch_item_master.ch_item_master.opening_stock_import import _normalise_rows


class TestOpeningStockImportSerialValidation(unittest.TestCase):
	"""Two reasons this never ran a single assertion.

	The class inherited from nothing, so unittest collected no tests from it.
	And importing erpnext.stock.doctype.item.test_item at module scope runs
	erpnext's test-record bootstrap on import, which tries to create its own
	Fiscal Years and dies here with "Year start date or end date is overlapping
	with Fiscal Year 2021-2022" -- so the module raised before it was even
	collected. That import is now made inside the test, where it is used.

	The fixtures it needs (_Test Company, _Test Warehouse - _TC) belong to
	erpnext's test bootstrap and are absent on a site restored from production,
	so the test skips with the reason rather than failing on a missing master.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		self._savepoint = "opening_stock_import_serial_test"
		frappe.db.savepoint(self._savepoint)

	def tearDown(self):
		frappe.db.rollback(save_point=self._savepoint)

	def test_missing_serial_no_is_blocked_during_opening_import_validation(self):
		item_code = "_Test Opening Import Serial"
		warehouse = "_Test Warehouse - _TC"
		company = "_Test Company"

		if not frappe.db.exists("Company", company):
			raise unittest.SkipTest("erpnext test fixture %r absent on this site" % company)
		if not frappe.db.exists("Warehouse", warehouse):
			raise unittest.SkipTest("erpnext test fixture %r absent on this site" % warehouse)

		from erpnext.stock.doctype.item.test_item import create_item

		create_item(item_code, warehouse=warehouse, company=company)
		item = frappe.get_doc("Item", item_code)
		item.has_serial_no = 1
		item.serial_no_series = "OPEN-IMPORT-SERIAL-.####"
		item.save()

		raw_rows = [
			{
				"company": company,
				"warehouse": warehouse,
				"item_code": item_code,
				"qty": "1",
				"valuation_rate": "100",
				"serial_no": "OPEN-IMPORT-SERIAL-0001",
			}
		]

		lines, errors, warnings = _normalise_rows(raw_rows, None)
		assert not lines, "missing serials should be rejected during row normalization"
		assert any("Serial No master" in message for message in errors), errors
