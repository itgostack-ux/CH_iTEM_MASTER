# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Serial Lifecycle — tracks IMEI / Serial Number devices
through their complete lifecycle:
  Received → In Stock → Displayed / Sold → Returned → In Service → Refurbished → Buyback → Scrapped

Each status change is logged in the lifecycle_log child table.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, now_datetime, getdate


# Valid lifecycle transitions: from_status → [allowed to_statuses]
VALID_TRANSITIONS = {
    "": ["Received"],
    "Received": ["In Stock", "Returned", "Scrapped"],
    "In Stock": ["Displayed", "Sold", "In Service", "Scrapped", "Lost"],
    "Displayed": ["In Stock", "Sold", "Scrapped", "Lost"],
    "Sold": ["Returned", "In Service"],
    "Returned": ["In Stock", "In Service", "Refurbished", "Buyback", "Scrapped"],
    "In Service": ["In Stock", "Refurbished", "Scrapped", "Returned"],
    "Refurbished": ["In Stock", "Buyback", "Scrapped"],
    "Buyback": ["In Stock", "Refurbished", "Scrapped"],
    "Scrapped": [],
    "Lost": [],
}


class CHSerialLifecycle(Document):
    def validate(self):
        self._validate_imei()
        self._auto_warranty_status()

    def before_save(self):
        if self.has_value_changed("lifecycle_status"):
            old_status = self.get_db_value("lifecycle_status") or ""
            new_status = self.lifecycle_status
            self._validate_transition(old_status, new_status)
            self._log_status_change(old_status, new_status)

    def _validate_imei(self):
        """Validate IMEI format — 15 digits if provided."""
        for field in ("imei_number", "imei_number_2"):
            val = self.get(field)
            if val:
                cleaned = val.strip().replace(" ", "").replace("-", "")
                if not cleaned.isdigit() or len(cleaned) != 15:
                    frappe.throw(
                        _(f"{self.meta.get_label(field)}: IMEI must be exactly 15 digits. Got: {val}"),
                        title=_("Invalid IMEI"),
                    )
                self.set(field, cleaned)

    def _validate_transition(self, old_status, new_status):
        """Ensure the status change follows valid lifecycle paths."""
        if not old_status and new_status:
            # First time setting — allow any initial status
            return

        allowed = VALID_TRANSITIONS.get(old_status, [])
        if new_status not in allowed:
            frappe.throw(
                _("Cannot move from <b>{0}</b> to <b>{1}</b>. "
                  "Allowed transitions: {2}").format(
                    old_status, new_status, ", ".join(allowed) or "None"
                ),
                title=_("Invalid Lifecycle Transition"),
            )

    def _log_status_change(self, old_status, new_status):
        """Append a row to lifecycle_log child table."""
        self.append("lifecycle_log", {
            "log_timestamp": now_datetime(),
            "from_status": old_status,
            "to_status": new_status,
            "changed_by": frappe.session.user,
            "company": self.current_company,
            "warehouse": self.current_warehouse,
            "remarks": self.notes or "",
        })

    def _auto_warranty_status(self):
        """Auto-compute warranty_status from dates."""
        if not self.warranty_end_date:
            return

        today = getdate(nowdate())
        ext_end = getdate(self.extended_warranty_end) if self.extended_warranty_end else None

        if ext_end and today <= ext_end:
            self.warranty_status = "Extended"
        elif today <= getdate(self.warranty_end_date):
            self.warranty_status = "Under Warranty"
        else:
            self.warranty_status = "Expired"


# ── Whitelisted API methods ─────────────────────────────────────────────────

@frappe.whitelist()
def update_lifecycle_status(serial_no, new_status, company=None,
                            warehouse=None, remarks=None):
    """Change lifecycle status of a serial number.

    Args:
        serial_no: CH Serial Lifecycle name (= serial number)
        new_status: Target lifecycle status
        company: Optional — current company
        warehouse: Optional — current warehouse
        remarks: Optional — reason for change
    """
    doc = frappe.get_doc("CH Serial Lifecycle", serial_no)
    doc.lifecycle_status = new_status
    if company:
        doc.current_company = company
    if warehouse:
        doc.current_warehouse = warehouse
    if remarks:
        doc.notes = remarks
    doc.save(ignore_permissions=False)
    frappe.db.commit()
    return {"status": "ok", "serial_no": doc.name, "new_status": doc.lifecycle_status}


@frappe.whitelist()
def get_lifecycle_history(serial_no):
    """Get full lifecycle history of a device."""
    doc = frappe.get_doc("CH Serial Lifecycle", serial_no)
    return {
        "serial_no": doc.serial_no,
        "item_code": doc.item_code,
        "item_name": doc.item_name,
        "lifecycle_status": doc.lifecycle_status,
        "warranty_status": doc.warranty_status,
        "log": [
            {
                "timestamp": str(row.log_timestamp),
                "from_status": row.from_status,
                "to_status": row.to_status,
                "changed_by": row.changed_by,
                "company": row.company,
                "warehouse": row.warehouse,
                "remarks": row.remarks,
            }
            for row in doc.lifecycle_log
        ],
    }


@frappe.whitelist()
def scan_serial(serial_no):
    """Quick lookup by serial/IMEI — returns summary for mobile scanning.

    Searches by serial_no, imei_number, or imei_number_2.
    """
    # Try exact match on name
    if frappe.db.exists("CH Serial Lifecycle", serial_no):
        doc = frappe.get_doc("CH Serial Lifecycle", serial_no)
    else:
        # Search by IMEI fields
        name = frappe.db.get_value(
            "CH Serial Lifecycle",
            {"imei_number": serial_no},
            "name"
        ) or frappe.db.get_value(
            "CH Serial Lifecycle",
            {"imei_number_2": serial_no},
            "name"
        )
        if not name:
            frappe.throw(_("Serial / IMEI not found: {0}").format(serial_no))
        doc = frappe.get_doc("CH Serial Lifecycle", name)

    # Valid next transitions
    allowed_next = VALID_TRANSITIONS.get(doc.lifecycle_status, [])

    return {
        "serial_no": doc.serial_no,
        "item_code": doc.item_code,
        "item_name": doc.item_name,
        "model": doc.ch_model,
        "lifecycle_status": doc.lifecycle_status,
        "sub_status": doc.sub_status,
        "warranty_status": doc.warranty_status,
        "warranty_end_date": str(doc.warranty_end_date) if doc.warranty_end_date else None,
        "current_company": doc.current_company,
        "current_warehouse": doc.current_warehouse,
        "current_store": doc.current_store,
        "customer": doc.customer,
        "customer_name": doc.customer_name,
        "allowed_transitions": allowed_next,
        "purchase_rate": doc.purchase_rate,
        "sale_rate": doc.sale_rate,
        "service_count": doc.service_count,
        "last_change": str(doc.lifecycle_log[-1].log_timestamp) if doc.lifecycle_log else None,
    }
