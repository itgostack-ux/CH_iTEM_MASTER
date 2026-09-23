import unittest

import frappe

from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog


class TestCHOTPLog(unittest.TestCase):
    """Plain unittest.TestCase, wrapped in a savepoint.

    IntegrationTestCase pulls in erpnext's test-record bootstrap, which tries
    to create its own Fiscal Years and dies on this site with "Year start date
    or end date is overlapping with Fiscal Year 2021-2022" -- before a single
    assertion runs. The bench convention is unittest.TestCase plus a savepoint
    for exactly this reason; the savepoint also guarantees the cleanup these
    tests used to do by hand, which a failing assertion would have skipped.
    """

    def setUp(self):
        frappe.set_user("Administrator")
        self._savepoint = "ch_otp_log_test"
        frappe.db.savepoint(self._savepoint)

    def tearDown(self):
        frappe.db.rollback(save_point=self._savepoint)

    def test_generate_and_verify(self):
        mobile = "9876500001"
        purpose = "Buyback Confirmation"

        otp_code = CHOTPLog.generate_otp(mobile, purpose)
        self.assertEqual(len(otp_code), 6)
        self.assertTrue(otp_code.isdigit())

        # Verify with correct code
        result = CHOTPLog.verify_otp(mobile, purpose, otp_code)
        self.assertTrue(result["valid"])

        # Cleanup
        for name in frappe.get_all("CH OTP Log", filters={"mobile_no": mobile}, pluck="name"):
            frappe.delete_doc("CH OTP Log", name, force=True)

    def test_wrong_otp(self):
        mobile = "9876500002"
        purpose = "Buyback Confirmation"

        CHOTPLog.generate_otp(mobile, purpose)
        result = CHOTPLog.verify_otp(mobile, purpose, "000000")
        self.assertFalse(result["valid"])

        # Cleanup
        for name in frappe.get_all("CH OTP Log", filters={"mobile_no": mobile}, pluck="name"):
            frappe.delete_doc("CH OTP Log", name, force=True)
