import random
import string

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, add_to_date

from buyback.utils import validate_indian_phone


class CHOTPLog(Document):
    def validate(self):
        if self.mobile_no:
            self.mobile_no = validate_indian_phone(self.mobile_no, "Mobile No")

    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('ch_otp_log_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(otp_id) FROM `tabCH OTP Log`"
            )[0][0] or 0
            self.otp_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('ch_otp_log_id')")

        if not self.generated_at:
            self.generated_at = now_datetime()
        if not self.expires_at:
            self.expires_at = add_to_date(self.generated_at, minutes=5)
        if not self.generated_by:
            self.generated_by = frappe.session.user
        self.status = "Pending"
        self.attempts = 0

    @staticmethod
    def generate_otp(mobile_no, purpose, reference_doctype=None, reference_name=None):
        """Create a new OTP log entry and return the OTP code.

        Usage:
            otp = CHOTPLog.generate_otp("9876543210", "Buyback Confirmation",
                                         "Buyback Order", "BO-00001")
        """
        otp_code = "".join(random.choices(string.digits, k=6))

        # BB-3 fix: Rate limit OTP generation — max 5 OTPs per mobile per hour
        recent_count = frappe.db.count("CH OTP Log", {
            "mobile_no": mobile_no,
            "generated_at": (">=", add_to_date(now_datetime(), hours=-1)),
        })
        if recent_count >= 5:
            frappe.throw(
                _("Too many OTP requests for {0}. Please wait before trying again.").format(mobile_no),
                title=_("Rate Limit Exceeded"),
            )

        doc = frappe.get_doc({
            "doctype": "CH OTP Log",
            "mobile_no": mobile_no,
            "otp_code": otp_code,
            "purpose": purpose,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "ip_address": frappe.local.request_ip if hasattr(frappe.local, "request_ip") else None,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

        return otp_code

    @staticmethod
    def verify_otp(mobile_no, purpose, otp_code, reference_doctype=None, reference_name=None):
        """Verify an OTP against stored records.

        Returns:
            dict: {"valid": bool, "message": str}
        """
        MAX_ATTEMPTS = 5

        filters = {
            "mobile_no": mobile_no,
            "purpose": purpose,
            "status": "Pending",
        }
        if reference_doctype:
            filters["reference_doctype"] = reference_doctype
        if reference_name:
            filters["reference_name"] = reference_name

        logs = frappe.get_all(
            "CH OTP Log",
            filters=filters,
            fields=["name", "otp_code", "expires_at", "attempts"],
            order_by="creation desc",
            limit=1,
        )

        if not logs:
            return {"valid": False, "message": _("No pending OTP found for this mobile number.")}

        log = logs[0]
        doc = frappe.get_doc("CH OTP Log", log.name)

        # Check expiry
        if now_datetime() > doc.expires_at:
            doc.status = "Expired"
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            return {"valid": False, "message": _("OTP has expired. Please request a new one.")}

        # Check max attempts
        if doc.attempts >= MAX_ATTEMPTS:
            doc.status = "Failed"
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            return {"valid": False, "message": _("Maximum OTP attempts exceeded.")}

        doc.attempts += 1

        # Verify code
        if doc.get_password("otp_code") == otp_code:
            doc.status = "Verified"
            doc.verified_at = now_datetime()
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            return {"valid": True, "message": _("OTP verified successfully.")}
        else:
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            remaining = MAX_ATTEMPTS - doc.attempts
            return {
                "valid": False,
                "message": _("Invalid OTP. {0} attempt(s) remaining.").format(remaining),
            }
