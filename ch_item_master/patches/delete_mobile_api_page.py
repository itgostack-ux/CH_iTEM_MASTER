# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Migration patch: Remove the ch-mobile-api-tester Page record.

The Mobile API Tester page was removed from the codebase (replaced by FastAPI).
Without this patch, bench migrate would crash with FileNotFoundError
on sites that still have the Page record in the database.
"""

import frappe


def execute():
    if frappe.db.exists("Page", "ch-mobile-api-tester"):
        frappe.delete_doc("Page", "ch-mobile-api-tester", force=True)
        frappe.db.commit()
