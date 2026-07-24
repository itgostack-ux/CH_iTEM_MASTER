import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CHWarrantyPaymentAttempt(Document):
	def validate(self):
		self.provider = (self.provider or "").strip().lower()
		if self.provider not in {"razorpay", "cashfree", "payu"}:
			frappe.throw(_("Unsupported warranty payment provider."))
		if flt(self.amount) <= 0:
			frappe.throw(_("Warranty payment attempt amount must be positive."))
		if not self.status:
			self.status = "Created"

