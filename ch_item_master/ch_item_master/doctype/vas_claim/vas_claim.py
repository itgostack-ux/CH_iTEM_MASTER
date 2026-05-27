import frappe
from frappe.model.document import Document


class VASClaim(Document):
	def validate(self):
		if self.source_warranty_claim and frappe.db.exists("CH Warranty Claim", self.source_warranty_claim):
			src = frappe.get_cached_doc("CH Warranty Claim", self.source_warranty_claim)
			self.claim_status = self.claim_status or src.claim_status or "Open"
			self.customer = self.customer or src.customer
			self.sales_invoice = self.sales_invoice or src.gogizmo_invoice
			self.approved_amount = self.approved_amount or src.approved_amount
