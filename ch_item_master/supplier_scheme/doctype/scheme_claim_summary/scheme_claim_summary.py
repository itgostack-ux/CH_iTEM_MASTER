"""Scheme Claim Summary — aggregated claim for a scheme period."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SchemeClaimSummary(Document):
	def validate(self):
		self._compute_tds()
		self._validate_amounts()

	def on_submit(self):
		if self.claim_status == "Draft":
			self.db_set("claim_status", "Claim Raised")

	def on_cancel(self):
		self.db_set("claim_status", "Draft")

	def _compute_tds(self):
		self.total_payout = flt(self.base_payout) + flt(self.additional_payout)
		if flt(self.tds_percent) > 0:
			self.tds_amount = flt(self.total_payout * flt(self.tds_percent) / 100, 2)
		else:
			self.tds_amount = 0
		self.net_claim = flt(self.total_payout) - flt(self.tds_amount)

	def _validate_amounts(self):
		if flt(self.total_payout) < 0:
			frappe.throw(_("Total Payout cannot be negative"))
		if flt(self.net_claim) < 0:
			frappe.throw(_("Net Claim cannot be negative"))

	@frappe.whitelist()
	def lock_claim(self):
		"""Lock the claim so no more achievement changes affect it."""
		if self.claim_status != "Draft":
			frappe.throw(_("Only Draft claims can be locked"))
		self.db_set("claim_status", "Locked")
		self.reload()

	@frappe.whitelist()
	def mark_claim_ready(self):
		"""Mark as ready for filing with the supplier."""
		if self.claim_status not in ("Draft", "Locked"):
			frappe.throw(_("Only Draft or Locked claims can be marked ready"))
		self.db_set("claim_status", "Claim Ready")
		self.reload()
