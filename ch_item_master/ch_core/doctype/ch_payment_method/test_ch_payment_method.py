import frappe
from frappe.tests import IntegrationTestCase


class TestCHPaymentMethod(IntegrationTestCase):
    def test_auto_id(self):
        doc = frappe.get_doc({
            "doctype": "CH Payment Method",
            "method_name": "_Test Payment Cash",
            "method_type": "Cash",
        })
        doc.insert()
        self.assertGreater(doc.payment_method_id, 0)
        doc.delete()

    def test_auto_flags_bank(self):
        doc = frappe.get_doc({
            "doctype": "CH Payment Method",
            "method_name": "_Test Payment Bank",
            "method_type": "Bank",
        })
        doc.insert()
        self.assertEqual(doc.requires_bank_details, 1)
        self.assertEqual(doc.requires_upi_id, 0)
        doc.delete()
