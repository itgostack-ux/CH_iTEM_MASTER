import frappe
from frappe.model.document import Document


class CHStoreZone(Document):
    def validate(self):
        from ch_item_master.ch_core.location_hierarchy import validate_zone_source_warehouse

        validate_zone_source_warehouse(self)
