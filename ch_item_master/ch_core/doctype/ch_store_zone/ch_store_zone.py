import frappe
from frappe.model.document import Document


class CHStoreZone(Document):
    def validate(self):
        if self.company and self.city:
            city_company = frappe.db.get_value("CH City", self.city, "company")
            if city_company and city_company != self.company:
                frappe.throw(
                    frappe._("City {0} belongs to company {1}, not {2}.").format(
                        frappe.bold(self.city), frappe.bold(city_company), frappe.bold(self.company)
                    ),
                    title=frappe._("Invalid City"),
                )
