import frappe
from frappe.model.rename_doc import rename_doc


OLD_DTYPE = "CH " + "Sold" + " Plan"
NEW_DTYPE = "Active VAS Plans"


def execute():
    """Rename legacy VAS plan DocType to Active VAS Plans before model sync."""
    if not frappe.db.exists("DocType", OLD_DTYPE):
        return

    if frappe.db.exists("DocType", NEW_DTYPE):
        return

    rename_doc("DocType", OLD_DTYPE, NEW_DTYPE, force=True, ignore_permissions=True)
