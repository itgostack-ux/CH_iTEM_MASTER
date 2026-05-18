"""Print format setup for CH Item Master service/warranty flows."""

import frappe


DEVICE_RECEIVED_RECEIPT_HTML = """
<style>
  .claim-receipt { font-family: Arial, sans-serif; font-size: 12px; color: #111; }
  .claim-title { font-size: 18px; font-weight: 700; text-align: center; margin-bottom: 6px; }
  .claim-subtitle { text-align: center; color: #555; margin-bottom: 14px; }
  .claim-grid { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
  .claim-grid td, .claim-grid th { border: 1px solid #222; padding: 6px 8px; vertical-align: top; }
  .claim-grid th { background: #f2f2f2; text-align: left; }
  .claim-small { font-size: 10px; color: #555; }
  .claim-sign { height: 52px; }
</style>

<div class="claim-receipt">
  <div class="claim-title">Device Received Receipt</div>
  <div class="claim-subtitle">Warranty / service claim intake acknowledgement</div>

  <table class="claim-grid">
    <tr>
      <th width="25%">Receipt No</th><td width="25%">{{ doc.name }}</td>
      <th width="25%">Claim ID</th><td width="25%">{{ doc.claim_id or "" }}</td>
    </tr>
    <tr>
      <th>Received At</th><td>{{ frappe.utils.format_datetime(doc.device_received_at) if doc.device_received_at else "" }}</td>
      <th>Received By</th><td>{{ doc.device_received_by or "" }}</td>
    </tr>
    <tr>
      <th>Company</th><td>{{ doc.company or "" }}</td>
      <th>Status</th><td>{{ doc.claim_status or "" }}</td>
    </tr>
  </table>

  <table class="claim-grid">
    <tr><th colspan="4">Customer</th></tr>
    <tr>
      <th width="25%">Name</th><td width="25%">{{ doc.customer_name or doc.customer or "" }}</td>
      <th width="25%">Phone</th><td width="25%">{{ doc.customer_phone or "" }}</td>
    </tr>
    <tr>
      <th>Service Mode</th><td>{{ doc.mode_of_service or "" }}</td>
      <th>Pickup Tracking</th><td>{{ doc.pickup_tracking_no or "" }}</td>
    </tr>
  </table>

  <table class="claim-grid">
    <tr><th colspan="4">Device Intake Details</th></tr>
    <tr>
      <th width="25%">Item</th><td width="25%">{{ doc.item_code or "" }}</td>
      <th width="25%">IMEI / Serial</th><td width="25%">{{ doc.serial_no or doc.imei_number or "" }}</td>
    </tr>
    <tr>
      <th>IMEI Verified</th><td>{{ "Yes" if doc.imei_verified else "No" }}</td>
      <th>Intake QC</th><td>{{ doc.intake_qc_status or "Pending" }}</td>
    </tr>
    <tr>
      <th>Condition On Receipt</th><td colspan="3">{{ doc.condition_on_receipt or "" }}</td>
    </tr>
    <tr>
      <th>Accessories Received</th><td colspan="3">{{ doc.accessories_received or "" }}</td>
    </tr>
    <tr>
      <th>Issue Reported</th><td colspan="3">{{ doc.issue_description or "" }}</td>
    </tr>
    <tr>
      <th>Receiving Remarks</th><td colspan="3">{{ doc.receiving_remarks or "" }}</td>
    </tr>
  </table>

  <table class="claim-grid">
    <tr><th colspan="4">Coverage / Fee</th></tr>
    <tr>
      <th width="25%">Coverage Type</th><td width="25%">{{ doc.coverage_type or "" }}</td>
      <th width="25%">Fee Status</th><td width="25%">{{ doc.processing_fee_status or "" }}</td>
    </tr>
    <tr>
      <th>Processing Fee</th><td colspan="3">{{ frappe.format(doc.processing_fee_amount or 0, {"fieldtype": "Currency"}) }}</td>
    </tr>
  </table>

  <table class="claim-grid">
    <tr>
      <td width="50%"><b>Customer Signature</b><div class="claim-sign"></div></td>
      <td width="50%"><b>Store / Service Staff Signature</b><div class="claim-sign"></div></td>
    </tr>
  </table>

  <div class="claim-small">
    This is a device intake acknowledgement, not a repair completion certificate or tax invoice.
    Customer confirms that device condition, accessories, serial/IMEI and issue details above are correct.
  </div>
</div>
""".strip()


def _upsert_print_format(name, doc_type, html):
    values = {
        "doctype": "Print Format",
        "name": name,
        "doc_type": doc_type,
        "module": "CH Item Master",
        "print_format_type": "Jinja",
        "print_format_for": "DocType",
        "custom_format": 1,
        "standard": "No",
        "disabled": 0,
        "html": html,
        "css": "",
    }
    if frappe.db.exists("Print Format", name):
        doc = frappe.get_doc("Print Format", name)
        changed = False
        for field, value in values.items():
            if field in ("doctype", "name"):
                continue
            if doc.get(field) != value:
                doc.set(field, value)
                changed = True
        if changed:
            doc.save(ignore_permissions=True)
    else:
        frappe.get_doc(values).insert(ignore_permissions=True)


def ensure_print_formats():
    """Ensure app-managed print formats for warranty/service intake exist."""
    _upsert_print_format(
        "Device Received Receipt",
        "CH Warranty Claim",
        DEVICE_RECEIVED_RECEIPT_HTML,
    )
    frappe.db.commit()
