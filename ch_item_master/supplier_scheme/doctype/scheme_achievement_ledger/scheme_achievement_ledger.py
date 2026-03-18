"""Scheme Achievement Ledger — one row per sale that matches an active scheme."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SchemeAchievementLedger(Document):
	def validate(self):
		self._check_duplicate_serial()
		self._check_demo_unit()
		self._evaluate_compliance()

	def _check_duplicate_serial(self):
		"""Same IMEI cannot be counted twice under the same scheme."""
		if not self.serial_no or self.is_reversed:
			return
		existing = frappe.db.exists(
			"Scheme Achievement Ledger",
			{
				"scheme": self.scheme,
				"serial_no": self.serial_no,
				"is_reversed": 0,
				"name": ("!=", self.name),
			},
		)
		if existing:
			frappe.throw(
				_("Serial No {0} already counted under scheme {1} (entry {2})").format(
					self.serial_no, self.scheme, existing
				)
			)

	def _check_demo_unit(self):
		if self.demo_unit:
			self.eligible_for_slab = 0
			self.eligible_for_payout = 0
			self.rejection_reason = (self.rejection_reason or "") + " Demo unit excluded."

	def _evaluate_compliance(self):
		"""Only mark eligible when compliance conditions are met."""
		if self.is_reversed:
			self.eligible_for_slab = 0
			self.eligible_for_payout = 0
			return

		reasons = []
		if not self.warranty_registered:
			reasons.append("Warranty not registered")
		if not self.crm_updated:
			reasons.append("CRM not updated")
		if self.demo_unit:
			reasons.append("Demo unit")

		if reasons:
			self.eligible_for_payout = 0
			self.rejection_reason = "; ".join(reasons)
		else:
			self.eligible_for_payout = 1
			if not self.rejection_reason or self.rejection_reason.strip() in (
				"Warranty not registered",
				"CRM not updated",
				"Demo unit",
				"Warranty not registered; CRM not updated",
			):
				self.rejection_reason = ""
