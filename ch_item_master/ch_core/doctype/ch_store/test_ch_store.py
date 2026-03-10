import frappe
from frappe.tests import IntegrationTestCase


class TestCHStore(IntegrationTestCase):
    def test_auto_id(self):
        doc = frappe.get_doc({
            "doctype": "CH Store",
            "store_code": "_TEST-STORE-001",
            "store_name": "Test Store",
            "company": frappe.db.get_value("Company", {}, "name"),
        })
        doc.insert()
        self.assertGreater(doc.store_id, 0)
        doc.delete()

    def test_store_code_uppercase(self):
        doc = frappe.get_doc({
            "doctype": "CH Store",
            "store_code": "test-lower",
            "store_name": "Test Lower Store",
            "company": frappe.db.get_value("Company", {}, "name"),
        })
        doc.insert()
        self.assertEqual(doc.store_code, "TEST-LOWER")
        doc.delete()
