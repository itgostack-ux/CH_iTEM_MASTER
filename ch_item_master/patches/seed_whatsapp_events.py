"""Seed the CH WhatsApp Event catalog (the ops triggers the apps fire) and
migrate any customised template names from the legacy single into the default
company's CH WhatsApp Template library.

The catalog's `default_template` is the fallback used when a company has no
mapped template, so outbound WhatsApp keeps working with zero per-company setup.
Idempotent — safe to re-run.
"""
import frappe

# (event_key, label, module, default_template, variables)
EVENTS = [
    ("buyback_otp", "Buyback OTP", "Buyback", "buyback_otp", "1=OTP code"),
    ("buyback_order_created", "Buyback Order Created", "Buyback", "buyback_order_created",
     "1=customer, 2=device, 3=price"),
    ("buyback_approved", "Buyback Order Approved", "Buyback", "buyback_approved",
     "1=customer, 2=device, 3=price"),
    ("buyback_customer_approval", "Buyback Customer Approval Link", "Buyback",
     "buyback_customer_approval", "1=customer, 2=device, 3=price, 4=approval URL"),
    ("buyback_paid", "Buyback Payment Done", "Buyback", "buyback_paid",
     "1=customer, 2=device, 3=amount"),
    ("gofix_device_received", "GoFix Device Received", "GoFix", "gofix_device_received",
     "1=customer, 2=SR, 3=device, 4=ETA"),
    ("gofix_repair_completed", "GoFix Repair Completed", "GoFix", "gofix_repair_completed",
     "1=customer, 2=SR, 3=device"),
    ("gofix_ready_for_delivery", "GoFix Ready for Delivery", "GoFix", "gofix_ready_delivery",
     "1=customer, 2=SR, 3=device"),
    ("gofix_sla_breach", "GoFix SLA Breach Apology", "GoFix", "gofix_sla_breach",
     "1=customer, 2=SR, 3=device"),
    ("gofix_not_repairable", "GoFix Not Repairable / BER", "GoFix", "gofix_not_repairable",
     "1=customer, 2=SR, 3=device, 4=status, 5=reason"),
    ("gofix_estimate_approval", "GoFix Estimate Approval", "GoFix", "gofix_estimate_approval",
     "1=customer, 2=SR, 3=device, 4=amount, 5=version, 6=tracking URL"),
    ("gofix_revised_estimate", "GoFix Revised Estimate", "GoFix", "gofix_revised_estimate",
     "1=customer, 2=SR, 3=device, 4=amount, 5=version, 6=tracking URL"),
    ("gofix_tracking_link", "GoFix Tracking Link", "GoFix", "gofix_tracking_link",
     "1=customer, 2=SR, 3=device, 4=tracking URL"),
    ("general_otp", "General OTP", "General", "ch_otp_verification", "1=OTP code"),
    ("invoice_receipt", "Invoice Receipt / Share", "POS", "invoice_receipt",
     "1=customer, 2=invoice, 3=amount, 4=PDF URL"),
    ("transfer_status", "Logistics Transfer Status", "Logistics", "transfer_status",
     "1=manifest, 2=destination, 3=tracking URL"),
]

# legacy single field -> event key, for migrating overridden names
SINGLE_FIELD_TO_EVENT = {
    "buyback_otp": "buyback_otp",
    "buyback_order_created": "buyback_order_created",
    "buyback_approved": "buyback_approved",
    "buyback_customer_approval": "buyback_customer_approval",
    "buyback_paid": "buyback_paid",
    "gofix_device_received": "gofix_device_received",
    "gofix_repair_completed": "gofix_repair_completed",
    "gofix_ready_for_delivery": "gofix_ready_for_delivery",
    "gofix_sla_breach": "gofix_sla_breach",
    "general_otp": "general_otp",
}


def execute():
    if not frappe.db.exists("DocType", "CH WhatsApp Event"):
        return

    # 1) upsert the event catalog
    for key, label, module, default_template, variables in EVENTS:
        if frappe.db.exists("CH WhatsApp Event", key):
            doc = frappe.get_doc("CH WhatsApp Event", key)
        else:
            doc = frappe.new_doc("CH WhatsApp Event")
            doc.event_key = key
        doc.label, doc.module = label, module
        doc.default_template, doc.variables = default_template, variables
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)

    # 2) migrate genuinely-customised template names into the default company's library
    if not (frappe.db.exists("DocType", "CH WhatsApp Template")
            and frappe.db.exists("DocType", "CH WhatsApp Settings")):
        frappe.db.commit()
        return
    company = (frappe.defaults.get_global_default("company")
               or frappe.db.get_value("Company", {}, "name"))
    if not company:
        frappe.db.commit()
        return

    single = frappe.get_single("CH WhatsApp Settings")
    defaults = {e[0]: e[3] for e in EVENTS}
    for field, event_key in SINGLE_FIELD_TO_EVENT.items():
        val = (single.get(field) or "").strip()
        if not val or val == defaults.get(event_key):
            continue  # nothing set, or just the conventional default → catalog covers it
        if frappe.db.exists("CH WhatsApp Template", {"company": company, "event": event_key}):
            continue
        frappe.get_doc({
            "doctype": "CH WhatsApp Template", "company": company, "event": event_key,
            "template_name": val, "language": "en", "enabled": 1,
        }).insert(ignore_permissions=True)

    frappe.db.commit()
