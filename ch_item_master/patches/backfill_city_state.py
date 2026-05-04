import frappe


def execute():
    if not frappe.db.exists("DocType", "CH City"):
        return

    frappe.db.sql(
        """UPDATE `tabCH City`
           SET state = 'Tamil Nadu'
           WHERE city_name = 'Chennai'
             AND IFNULL(state, '') = ''"""
    )
    frappe.clear_cache(doctype="CH City")
