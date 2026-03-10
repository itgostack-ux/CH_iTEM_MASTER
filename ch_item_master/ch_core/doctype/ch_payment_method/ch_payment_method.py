import frappe
from frappe.model.document import Document


class CHPaymentMethod(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('ch_payment_method_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(payment_method_id) FROM `tabCH Payment Method`"
            )[0][0] or 0
            self.payment_method_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('ch_payment_method_id')")

    def validate(self):
        """Auto-set requirement flags based on method_type."""
        if self.method_type == "Bank":
            self.requires_bank_details = 1
            self.requires_upi_id = 0
        elif self.method_type == "UPI":
            self.requires_upi_id = 1
            self.requires_bank_details = 0
        elif self.method_type == "Cash":
            self.requires_bank_details = 0
            self.requires_upi_id = 0
