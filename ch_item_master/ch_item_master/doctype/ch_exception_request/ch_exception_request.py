# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, add_to_date, getdate, flt


class CHExceptionRequest(Document):
	def before_insert(self):
		self.raised_at = now_datetime()
		self.requested_by = self.requested_by or frappe.session.user
		self.ip_address = frappe.local.request.remote_addr if frappe.local.request else ""

		if self.requested_by:
			self.requested_by_name = frappe.db.get_value(
				"User", self.requested_by, "full_name"
			) or ""

		if self.item_code and not self.item_name:
			self.item_name = frappe.db.get_value("Item", self.item_code, "item_name") or ""

		if self.customer and not self.customer_name:
			self.customer_name = frappe.db.get_value(
				"Customer", self.customer, "customer_name"
			) or ""

	def validate(self):
		self._validate_exception_type()
		self._check_daily_limit()

	def before_submit(self):
		if self.status == "Pending":
			frappe.throw(
				_("Cannot submit a Pending exception. Approve or Reject first.")
			)

	def _validate_exception_type(self):
		"""Ensure the exception type is enabled and applicable for this company."""
		etype = frappe.get_cached_doc("CH Exception Type", self.exception_type)
		if not etype.enabled:
			frappe.throw(
				_("Exception type {0} is disabled").format(self.exception_type)
			)

		company_name = self.company
		ggr_match = "GoGizmo" in (company_name or "")
		gfs_match = "GoFix" in (company_name or "")

		if ggr_match and not etype.applicable_to_ggr:
			frappe.throw(
				_("Exception type {0} is not applicable to {1}").format(
					self.exception_type, company_name
				)
			)
		if gfs_match and not etype.applicable_to_gfs:
			frappe.throw(
				_("Exception type {0} is not applicable to {1}").format(
					self.exception_type, company_name
				)
			)

	def _check_daily_limit(self):
		"""Check if max daily occurrences per store have been exceeded."""
		if not self.store_warehouse:
			return

		etype = frappe.get_cached_doc("CH Exception Type", self.exception_type)
		max_per_day = etype.max_occurrences_per_day or 0
		if max_per_day <= 0:
			return

		today_start = getdate()
		count = frappe.db.count("CH Exception Request", {
			"exception_type": self.exception_type,
			"store_warehouse": self.store_warehouse,
			"raised_at": (">=", str(today_start)),
			"name": ("!=", self.name or ""),
			"docstatus": ("!=", 2),
		})

		if count >= max_per_day:
			frappe.throw(
				_("Daily limit of {0} for {1} at this store has been reached").format(
					max_per_day, self.exception_type
				)
			)

	def approve(self, approver=None, channel=None, otp_reference=None,
	            resolution_value=None, remarks=None):
		"""Approve this exception request."""
		approver = approver or frappe.session.user
		now = now_datetime()

		self.status = "Approved"
		self.approver = approver
		self.approver_name = frappe.db.get_value("User", approver, "full_name") or ""
		self.approval_channel = channel or "Manager PIN"
		self.approved_at = now
		self.resolved_at = now
		self.resolved_by = approver

		if otp_reference:
			self.otp_reference = otp_reference

		if resolution_value is not None:
			self.resolution_value = flt(resolution_value)
		if remarks:
			self.resolution_remarks = remarks

		# Set expiry based on exception type config
		etype = frappe.get_cached_doc("CH Exception Type", self.exception_type)
		validity_minutes = etype.validity_minutes or 30
		self.approval_expiry = add_to_date(now, minutes=validity_minutes)

		self.save(ignore_permissions=True)
		self.submit()
		return self

	def reject(self, approver=None, reason=None):
		"""Reject this exception request."""
		approver = approver or frappe.session.user
		now = now_datetime()

		self.status = "Rejected"
		self.approver = approver
		self.approver_name = frappe.db.get_value("User", approver, "full_name") or ""
		self.resolved_at = now
		self.resolved_by = approver
		if reason:
			self.resolution_remarks = reason

		self.save(ignore_permissions=True)
		self.submit()
		return self

	def is_valid(self):
		"""Check if this approved exception is still within its validity window."""
		if self.status != "Approved" or self.docstatus != 1:
			return False
		if self.approval_expiry and now_datetime() > self.approval_expiry:
			return False
		return True
