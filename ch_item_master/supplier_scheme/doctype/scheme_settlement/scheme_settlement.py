"""Scheme Settlement — tracks receipt of supplier credit notes / payments against claims."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SchemeSettlement(Document):
	def validate(self):
		self._compute_pending()

	def on_submit(self):
		self._update_claim_status()
		self._update_scheme_totals()

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self._update_claim_status()
		self._update_scheme_totals()

	def _compute_pending(self):
		self.pending_amount = flt(self.claim_amount) - flt(self.tds_deducted) - flt(self.received_amount)
		if flt(self.received_amount) <= 0:
			self.status = "Pending"
		elif flt(self.pending_amount) > 0:
			self.status = "Partially Received"
		else:
			self.status = "Settled"

	def _update_claim_status(self):
		"""Update the linked Scheme Claim Summary status based on total settlements."""
		if not self.claim_summary:
			return

		total_received = frappe.db.sql("""
			SELECT IFNULL(SUM(received_amount), 0)
			FROM `tabScheme Settlement`
			WHERE claim_summary = %s AND docstatus = 1
		""", self.claim_summary)[0][0]

		claim_doc = frappe.get_doc("Scheme Claim Summary", self.claim_summary)
		net_claim = flt(claim_doc.net_claim)

		if flt(total_received) <= 0:
			new_status = "Claim Raised"
		elif flt(total_received) >= net_claim and net_claim > 0:
			new_status = "Settled"
		else:
			new_status = "Claim Raised"

		if claim_doc.claim_status != new_status:
			frappe.db.set_value("Scheme Claim Summary", self.claim_summary, "claim_status", new_status)

	def _update_scheme_totals(self):
		"""Trigger recomputation of settled/pending totals on the scheme."""
		if self.scheme:
			scheme = frappe.get_doc("Supplier Scheme Circular", self.scheme)
			scheme._compute_totals()
			scheme.db_set({
				"total_settled_amount": scheme.total_settled_amount,
				"total_pending_amount": scheme.total_pending_amount,
			})
