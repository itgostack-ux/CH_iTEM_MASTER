import re

import frappe
from frappe.model.document import Document

from ch_item_master.ch_item_master.utils import validate_indian_phone


class CHStore(Document):
    def autoname(self):
        """Auto-generate store_code: STO-{COMPANY_ABBR}-{CITY_SHORT}-####.

        Manual override is allowed (if user sets store_code explicitly we
        respect it). Otherwise we compose a deterministic, sortable code.
        """
        if not self.store_code:
            self.store_code = self._generate_store_code()
        else:
            self.store_code = self.store_code.strip().upper()
        self.name = self.store_code

    def _generate_store_code(self):
        company_abbr = (
            frappe.db.get_value("Company", self.company, "abbr") if self.company else None
        ) or "STO"
        city_short = ""
        if self.city:
            city_name = frappe.db.get_value("CH City", self.city, "city_name") or self.city
            city_short = re.sub(r"[^A-Za-z0-9]", "", city_name)[:6].upper()
        prefix_parts = ["STO", company_abbr.upper()]
        if city_short:
            prefix_parts.append(city_short)
        prefix = "-".join(prefix_parts) + "-"

        last = frappe.db.sql(
            "SELECT store_code FROM `tabCH Store` WHERE store_code LIKE %s ORDER BY creation DESC LIMIT 1",
            (prefix + "%",),
        )
        next_seq = 1
        if last and last[0][0]:
            try:
                next_seq = int(last[0][0].rsplit("-", 1)[-1]) + 1
            except ValueError:
                next_seq = 1
        return f"{prefix}{next_seq:04d}"

    def before_insert(self):
        """Auto-assign sequential integer ID using advisory lock."""
        frappe.db.sql("SELECT GET_LOCK('ch_store_id', 10)")
        try:
            last = frappe.db.sql(
                "SELECT MAX(store_id) FROM `tabCH Store`"
            )[0][0] or 0
            self.store_id = last + 1
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK('ch_store_id')")

    def validate(self):
        if self.store_code:
            self.store_code = self.store_code.strip().upper()

        if self.contact_phone:
            self.contact_phone = validate_indian_phone(self.contact_phone, "Contact Phone")

        if self.pincode and len(self.pincode.strip()) != 6:
            frappe.throw(
                frappe._("PIN Code must be exactly 6 digits."),
                title=frappe._("Invalid PIN Code"),
            )

        if self.city and self.company:
            city_company = frappe.db.get_value("CH City", self.city, "company")
            if city_company and city_company != self.company:
                frappe.throw(
                    frappe._("City {0} belongs to company {1}, not {2}.").format(
                        frappe.bold(self.city), frappe.bold(city_company), frappe.bold(self.company)
                    ),
                    title=frappe._("Invalid City"),
                )

        if self.zone:
            zone = frappe.db.get_value("CH Store Zone", self.zone, ["company", "city"], as_dict=True)
            if zone:
                if self.company and zone.company != self.company:
                    frappe.throw(
                        frappe._("Zone {0} belongs to company {1}, not {2}.").format(
                            frappe.bold(self.zone), frappe.bold(zone.company), frappe.bold(self.company)
                        ),
                        title=frappe._("Invalid Zone"),
                    )
                if self.city and zone.city and zone.city != self.city:
                    frappe.throw(
                        frappe._("Zone {0} belongs to city {1}, not {2}.").format(
                            frappe.bold(self.zone), frappe.bold(zone.city), frappe.bold(self.city)
                        ),
                        title=frappe._("Invalid Zone"),
                    )
