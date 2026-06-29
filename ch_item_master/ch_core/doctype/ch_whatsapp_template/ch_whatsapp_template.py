import frappe
from frappe.model.document import Document


class CHWhatsAppTemplate(Document):
    def validate(self):
        # One enabled template per (company, event)
        if self.enabled:
            dupe = frappe.db.exists('CH WhatsApp Template', {
                'company': self.company, 'event': self.event, 'enabled': 1,
                'name': ['!=', self.name]})
            if dupe:
                frappe.throw(f'An enabled template for {self.event} already exists for {self.company}: {dupe}')
