import secrets
import string

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import add_to_date, cint, now_datetime

from ch_item_master.ch_item_master.utils import validate_indian_phone


class CHOTPLog(Document):
    def validate(self):
        if self.mobile_no:
            self.mobile_no = validate_indian_phone(self.mobile_no, "Mobile No")

    def autoname(self):
        self.name = make_autoname(self.naming_series or "CHOTP-.#####", doc=self)
        suffix = self.name.rsplit("-", 1)[-1]
        if not suffix.isdigit():
            frappe.throw(_("CH OTP Log naming series must end in a numeric counter."))
        self.otp_id = cint(suffix)

    def before_insert(self):
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
        mobile_no = validate_indian_phone(mobile_no, "Mobile No")
        lock_name = f"ch_otp_generate:{mobile_no}"
        lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_name,))
        acquired = bool(lock_result and lock_result[0][0] == 1)
        if not acquired:
            frappe.throw(_("OTP generation is busy. Please retry."), frappe.ValidationError)

        try:
            recent_count = frappe.db.count("CH OTP Log", {
                "mobile_no": mobile_no,
                "generated_at": (">=", add_to_date(now_datetime(), hours=-1)),
                "status": ("!=", "Verified"),
            })
            if recent_count >= 5:
                frappe.throw(
                    _("Too many OTP requests for {0}. Please wait a few minutes before trying again.").format(mobile_no),
                    title=_("Rate Limit Exceeded"),
                )

            otp_code = "".join(secrets.choice(string.digits) for _ in range(6))
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
            return otp_code
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))

    @staticmethod
    def _stored_otp(doc) -> str:
        try:
            value = doc.get_password("otp_code") or ""
        except Exception:
            value = ""
        return str(value or doc.get("otp_code") or "").strip()

    @staticmethod
    def verify_otp(mobile_no, purpose, otp_code, reference_doctype=None, reference_name=None):
        """Verify an OTP against stored records.

        Returns:
            dict: {"valid": bool, "message": str}
        """
        from ch_item_master.ch_core.shadow_live import master_otp_matches

        MAX_ATTEMPTS = 5
        mobile_no = validate_indian_phone(mobile_no, "Mobile No")
        submitted_otp = str(otp_code or "").strip()

        filters = {
            "mobile_no": mobile_no,
            "purpose": purpose,
            "status": "Pending",
        }
        if reference_doctype:
            filters["reference_doctype"] = reference_doctype
        if reference_name:
            filters["reference_name"] = reference_name

        log_name = frappe.db.get_value(
            "CH OTP Log",
            filters,
            "name",
            order_by="creation desc",
            for_update=True,
        )

        if not log_name:
            return {"valid": False, "message": _("No pending OTP found for this mobile number.")}

        doc = frappe.get_doc("CH OTP Log", log_name)

        # Check expiry
        if now_datetime() > doc.expires_at:
            doc.status = "Expired"
            doc.save(ignore_permissions=True)
            return {"valid": False, "message": _("OTP has expired. Please request a new one.")}

        # Check max attempts
        if doc.attempts >= MAX_ATTEMPTS:
            doc.status = "Failed"
            doc.save(ignore_permissions=True)
            return {"valid": False, "message": _("Maximum OTP attempts exceeded.")}

        if master_otp_matches(submitted_otp):
            doc.status = "Verified"
            doc.verified_at = now_datetime()
            doc.save(ignore_permissions=True)
            return {
                "valid": True,
                "message": _("Verified via shadow-live master OTP."),
                "shadow_live": True,
                "otp_log": doc.name,
            }

        doc.attempts += 1
        if secrets.compare_digest(CHOTPLog._stored_otp(doc), submitted_otp):
            doc.status = "Verified"
            doc.verified_at = now_datetime()
            doc.save(ignore_permissions=True)
            return {
                "valid": True,
                "message": _("OTP verified successfully."),
                "otp_log": doc.name,
            }
        else:
            doc.save(ignore_permissions=True)
            remaining = MAX_ATTEMPTS - doc.attempts
            return {
                "valid": False,
                "message": _("Invalid OTP. {0} attempt(s) remaining.").format(remaining),
            }
