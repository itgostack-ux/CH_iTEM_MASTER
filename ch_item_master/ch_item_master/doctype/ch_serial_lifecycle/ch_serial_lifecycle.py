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

from ch_item_master.security import has_serial_lifecycle_permission


# Valid lifecycle transitions: from_status → [allowed to_statuses]
VALID_TRANSITIONS = {
    "": ["Received"],
    "Received": ["In Stock", "Returned", "Scrapped"],
    "In Stock": ["Displayed", "Sold", "In Service", "Buyback", "Scrapped", "Lost", "Repaired"],
    "Displayed": ["In Stock", "Sold", "Scrapped", "Lost"],
    "Sold": ["Returned", "In Service", "Buyback"],
    "Returned": ["In Stock", "In Service", "Refurbished", "Buyback", "Scrapped"],
    "In Service": ["Repaired", "In Stock", "Sold", "Refurbished", "Scrapped", "Returned"],
    "Repaired": ["Sold", "In Stock", "Returned", "In Service", "Refurbished", "Scrapped"],
    "Refurbished": ["In Stock", "Buyback", "Scrapped"],
    "Buyback": ["In Stock", "Sold", "In Service", "Refurbished", "Scrapped"],
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


def _is_system_write() -> bool:
    """True while a document-driven register update is in flight.

    Set only by update_lifecycle_status_for_document() below — a server-side
    frappe.flags marker that HTTP clients cannot inject. Market pattern
    (SAP material document → serial history, Oracle Install Base): once the
    business document has passed its own authorization gates, derived
    registers update with system privileges."""
    return bool(getattr(frappe.flags, "lifecycle_system_write", False))


def _require_lifecycle_access(doc, permission_type: str) -> None:
    if _is_system_write():
        return
    doc.check_permission(permission_type)
    if not has_serial_lifecycle_permission(
        doc=doc,
        user=frappe.session.user,
        permission_type=permission_type,
    ):
        frappe.throw(
            _("This serial lifecycle record is outside your assigned store scope."),
            frappe.PermissionError,
        )


def _validate_target_location(doc, company=None, warehouse=None) -> tuple[str | None, str | None]:
    effective_company = company or doc.current_company
    effective_warehouse = warehouse or doc.current_warehouse
    if effective_warehouse:
        warehouse_row = frappe.db.get_value(
            "Warehouse", effective_warehouse, ["company", "disabled"], as_dict=True
        )
        if not warehouse_row or warehouse_row.disabled:
            frappe.throw(_("The target warehouse is unavailable."), frappe.ValidationError)
        if effective_company and warehouse_row.company != effective_company:
            frappe.throw(_("The target warehouse belongs to another company."), frappe.ValidationError)
        effective_company = effective_company or warehouse_row.company
    if not _is_system_write():
        probe = frappe._dict(
            current_company=effective_company,
            current_warehouse=effective_warehouse,
        )
        if not has_serial_lifecycle_permission(doc=probe, user=frappe.session.user, permission_type="write"):
            frappe.throw(_("The target location is outside your assigned store scope."), frappe.PermissionError)
    return effective_company, effective_warehouse

@frappe.whitelist(methods=["POST"])
def update_lifecycle_status(serial_no, new_status, company=None,
                            warehouse=None, remarks=None, **kwargs) -> dict:
    """Change lifecycle status of a serial number.

    Args:
        serial_no: CH Serial Lifecycle name (= serial number)
        new_status: Target lifecycle status
        company: Optional — current company
        warehouse: Optional — current warehouse
        remarks: Optional — reason for change
        **kwargs: Additional fields to set on the document (e.g. sale_date,
                  sale_document, sale_rate, customer, customer_name)
    """
    if not frappe.db.exists("CH Serial Lifecycle", serial_no):
        frappe.log_error(
            title=f"Serial Lifecycle not found: {serial_no}",
            message=f"Cannot update lifecycle to '{new_status}' — CH Serial Lifecycle '{serial_no}' does not exist.",
        )
        return {"status": "skipped", "serial_no": serial_no, "reason": "not_found"}

    lock_key = f"serial_lifecycle_{frappe.scrub(str(serial_no))}"
    lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 30)", (lock_key,))[0][0]
    if not lock_result:
        frappe.log_error(
            f"Could not acquire lifecycle lock for {serial_no}",
            "Serial Lifecycle Lock",
        )
        return {"status": "skipped", "serial_no": serial_no, "reason": "lock_timeout"}
    try:
        doc = frappe.get_doc("CH Serial Lifecycle", serial_no)

        _require_lifecycle_access(doc, "write")
        company, warehouse = _validate_target_location(doc, company, warehouse)

        # P0 FIX: Re-read current status from DB inside the lock to detect races.
        # A concurrent request may have already advanced the status between the
        # caller's initial read and this point — use the fresh DB value as ground truth.
        _db_current_status = frappe.db.get_value(
            "CH Serial Lifecycle", serial_no, "lifecycle_status"
        ) or ""
        if _db_current_status != (doc.lifecycle_status or ""):
            frappe.throw(
                _("Serial {0} status changed concurrently from '{1}' to '{2}'. "
                  "Please reload and retry.").format(
                    serial_no, doc.lifecycle_status, _db_current_status
                ),
                title=_("Concurrent Status Change"),
            )

        doc.lifecycle_status = new_status
        if company:
            doc.current_company = company
        if warehouse:
            doc.current_warehouse = warehouse
        if remarks:
            doc.notes = remarks

        # Set any additional fields passed by caller
        allowed_extra_fields = {
            "sale_date", "sale_document", "sale_rate", "customer", "customer_name",
            "buyback_date", "buyback_value", "buyback_grade", "buyback_document",
            "stock_condition",
            "reference_doctype", "reference_name",  # gofix / warranty claim linkage
        }
        for key, value in kwargs.items():
            if key in allowed_extra_fields:
                doc.set(key, value)

        doc.save(ignore_permissions=_is_system_write())
        # v16: do not call frappe.db.commit() — caller or request lifecycle handles it
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))

    return {"status": "ok", "serial_no": doc.name, "new_status": doc.lifecycle_status}


def update_lifecycle_status_for_document(serial_no, new_status, **kwargs) -> dict:
    """System write for DOCUMENT-DRIVEN register updates.

    Use this from doc-event / workflow code (POS invoice submit, buyback
    close, service request custody, warranty claim) where the business
    document already passed its own authorization gates — the lifecycle
    register is a derived audit ledger, not a user document, so the
    operator does not additionally need write permission on it.

    Deliberately NOT whitelisted: direct client calls must keep using
    update_lifecycle_status(), which stays fully gated (role +
    CH User Scope). The privilege marker is a server-side flag that
    request payloads cannot inject.
    """
    frappe.flags.lifecycle_system_write = True
    try:
        return update_lifecycle_status(serial_no, new_status, **kwargs)
    finally:
        frappe.flags.lifecycle_system_write = False


@frappe.whitelist()
def get_lifecycle_history(serial_no) -> dict:
    """Get full lifecycle history of a device."""
    doc = frappe.get_doc("CH Serial Lifecycle", serial_no)
    _require_lifecycle_access(doc, "read")
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
def scan_serial(serial_no) -> dict:
    """Quick lookup by serial/IMEI — returns summary for mobile scanning.

    Searches by serial_no, imei_number, or imei_number_2.
    """
    # Use the shared resolver so name → imei_number → imei_number_2 order
    # is identical to CH Warranty Claim and warranty_api.
    from ch_item_master.ch_item_master.doctype.ch_warranty_claim.ch_warranty_claim import (
        resolve_lifecycle_name,
    )
    name = resolve_lifecycle_name(serial_no)
    if not name:
        frappe.throw(_("Serial / IMEI not found: {0}").format(serial_no), title=_("Ch Serial Lifecycle Error"))
    doc = frappe.get_doc("CH Serial Lifecycle", name)
    _require_lifecycle_access(doc, "read")

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
