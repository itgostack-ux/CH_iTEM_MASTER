"""Seed a per-company CH WhatsApp Account for the default company from the
legacy single CH WhatsApp Settings, so existing WhatsApp config carries over to
the new company-scoped model. Idempotent; the resolver falls back to the single
regardless, so this is a convenience migration (not required for correctness).
"""
import frappe


def execute():
    if not frappe.db.exists("DocType", "CH WhatsApp Account") \
            or not frappe.db.exists("DocType", "CH WhatsApp Settings"):
        return

    single = frappe.get_single("CH WhatsApp Settings")
    if not single or not int(single.get("enabled") or 0):
        return  # nothing configured to migrate

    company = (frappe.defaults.get_global_default("company")
               or frappe.db.get_value("Company", {}, "name"))
    if not company or frappe.db.exists("CH WhatsApp Account", company):
        return

    acct = frappe.new_doc("CH WhatsApp Account")
    acct.company = company
    for f in frappe.get_meta("CH WhatsApp Account").fields:
        fn = f.fieldname
        if fn == "company" or not single.meta.has_field(fn):
            continue
        if f.fieldtype == "Password":
            val = single.get_password(fn, raise_exception=False)
            if val:
                acct.set(fn, val)
        else:
            acct.set(fn, single.get(fn))
    acct.flags.ignore_permissions = True
    acct.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.logger().info(f"Seeded CH WhatsApp Account for {company} from single.")
