"""Register the warranty_claim_status_update WhatsApp event on existing sites.

`seed_whatsapp_events` is a one-shot patch that already ran, so adding the event
to its EVENTS list only covers fresh installs. This adds just the new catalog row
-- it deliberately does NOT re-run the full seeder, which would overwrite any
`default_template` an admin edited directly in the catalog.

CH Warranty Claim fires this on every customer-facing status transition; until a
company maps a template, send_template_message() logs "no template mapped" and
skips, so this is safe to apply before the provider template exists.
"""
import frappe

EVENT = {
    "event_key": "warranty_claim_status_update",
    "label": "Warranty Claim Status Update",
    "module": "Warranty",
    "default_template": "warranty_claim_status_update",
    "variables": "1=customer, 2=claim, 3=status, 4=message",
}


def execute():
    if not frappe.db.exists("DocType", "CH WhatsApp Event"):
        return
    if frappe.db.exists("CH WhatsApp Event", EVENT["event_key"]):
        return

    doc = frappe.new_doc("CH WhatsApp Event")
    doc.update(EVENT)
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
