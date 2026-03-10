import frappe
from frappe.tests import IntegrationTestCase

from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog


class TestCHOTPLog(IntegrationTestCase):
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
