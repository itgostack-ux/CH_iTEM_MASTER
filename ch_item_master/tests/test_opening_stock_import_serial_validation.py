import frappe

from ch_item_master.ch_item_master.opening_stock_import import _normalise_rows
from erpnext.stock.doctype.item.test_item import create_item


class TestOpeningStockImportSerialValidation:
	def test_missing_serial_no_is_blocked_during_opening_import_validation(self):
		item_code = "_Test Opening Import Serial"
		warehouse = "_Test Warehouse - _TC"
		company = "_Test Company"

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
