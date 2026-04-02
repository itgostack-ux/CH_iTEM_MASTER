# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, now_datetime, getdate, flt, random_string

from buyback.utils import validate_indian_phone


class CHVoucher(Document):
	def before_insert(self):
		if not self.voucher_code:
			self.voucher_code = self._generate_unique_code()
		if not self.issued_date:
			self.issued_date = now_datetime()
		if not self.issued_by:
			self.issued_by = frappe.session.user
		self.balance = flt(self.original_amount)
		self.status = "Draft"

	def validate(self):
		if self.phone:
			self.phone = validate_indian_phone(self.phone, "Phone")
		self._validate_amounts()
		self._validate_dates()
		self._auto_set_status()

	def _validate_amounts(self):
		if flt(self.original_amount) <= 0:
			frappe.throw(_("Original Amount must be greater than zero"))
		if flt(self.balance) < 0:
			frappe.throw(_("Balance cannot be negative"))
		if flt(self.balance) > flt(self.original_amount):
			frappe.throw(_("Balance cannot exceed Original Amount"))

	def _validate_dates(self):
		if self.valid_from and self.valid_upto and self.status != "Expired":
			if getdate(self.valid_upto) < getdate(self.valid_from):
				frappe.throw(_("Valid Upto cannot be before Valid From"))

	def _auto_set_status(self):
		today = getdate(nowdate())
		if self.status == "Cancelled":
			return
		if self.valid_upto and today > getdate(self.valid_upto):
			self.status = "Expired"
		elif flt(self.balance) <= 0:
			self.status = "Fully Used"
		elif flt(self.balance) < flt(self.original_amount):
			self.status = "Partially Used"
		elif self.docstatus == 0:
			self.status = "Draft"
		else:
			self.status = "Active"

	def on_submit(self):
		"""Submitting a voucher activates it."""
		self.status = "Active"
		self.db_set("status", "Active")

	def on_cancel(self):
		"""Cancelling a voucher forfeits remaining balance."""
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")

	@frappe.whitelist()
	def activate(self):
		"""Activate a draft voucher (legacy — now use Submit instead)."""
		if self.status not in ("Draft",):
			frappe.throw(_("Only Draft vouchers can be activated"))
		if self.docstatus == 0:
			self.submit()
		else:
			self.status = "Active"
			self.save()
		frappe.msgprint(_("Voucher {0} activated").format(self.voucher_code), indicator="green")

	@frappe.whitelist()
	def cancel_voucher(self):
		"""Cancel a voucher (forfeits remaining balance)."""
		if self.status in ("Fully Used", "Cancelled"):
			frappe.throw(_("Cannot cancel a {0} voucher").format(self.status))
		self.status = "Cancelled"
		self.save()

	def _generate_unique_code(self):
		"""Generate a unique 12-char alphanumeric voucher code."""
		for _ in range(10):
			code = random_string(12).upper()
			if not frappe.db.exists("CH Voucher", {"voucher_code": code}):
				return code
		frappe.throw(_("Unable to generate unique voucher code. Please try again."))
