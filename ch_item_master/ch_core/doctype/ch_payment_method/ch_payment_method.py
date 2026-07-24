import frappe
from frappe.model.document import Document

from ch_item_master.id_sequences import next_numeric_id


class CHPaymentMethod(Document):
    def before_insert(self):
        """Auto-assign the atomic sequential integration ID."""
        if not self.payment_method_id:
            self.payment_method_id = next_numeric_id("payment_method")

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
