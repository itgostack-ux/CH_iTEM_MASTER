import frappe

no_cache = 1


def get_context(context):
    context.no_cache = 1
    context.mobile_no = (frappe.form_dict.get("mobile_no") or "").strip()
