import frappe
from frappe.model.document import Document


class CHStore(Document):
    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('ch_store_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(store_id) FROM `tabCH Store`"
            )[0][0] or 0
            self.store_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('ch_store_id')")

    def validate(self):
        if self.store_code:
            self.store_code = self.store_code.strip().upper()

        if self.pincode and len(self.pincode.strip()) != 6:
            frappe.throw(
                frappe._("PIN Code must be exactly 6 digits."),
                title=frappe._("Invalid PIN Code"),
            )
