# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, cint
from frappe.model.document import Document


class CHVendorInfoRecord(Document):
	_APPROVAL_CONTEXT = object()
	_PROTECTED_FIELDS = (
		"approval_status",
		"submitted_by",
		"submitted_on",
		"approved_by",
		"approved_on",
	)
	APPROVAL_SENSITIVE_FIELDS = (
		"item_code",
		"supplier",
		"company",
		"purchase_org",
		"supplier_site",
		"preferred",
		"active",
		"source_rank",
		"allocation_pct",
		"vendor_item_code",
		"vendor_item_name",
		"currency",
		"standard_price",
		"price_valid_from",
		"price_valid_to",
		"lead_time_days",
		"min_order_qty",
		"price_breaks",
		"contracts",
	)

	def _authorize_approval_transition(self):
		self.flags.ch_vendor_info_approval_context = self._APPROVAL_CONTEXT

	def _has_approval_context(self):
		return self.flags.get("ch_vendor_info_approval_context") is self._APPROVAL_CONTEXT

	def _validate_approval_transition(self):
		if self._has_approval_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			if self.approval_status not in (None, "", "Draft") or any(
				self.get(fieldname) not in (None, "")
				for fieldname in self._PROTECTED_FIELDS
				if fieldname != "approval_status"
			):
				frappe.throw(
					_("Vendor approval state is set only by the approval workflow."),
					frappe.PermissionError,
				)
			return

		if any(self.get(fieldname) != before.get(fieldname) for fieldname in self._PROTECTED_FIELDS):
			frappe.throw(
				_("Vendor approval state can only be changed through Submit or Approve."),
				frappe.PermissionError,
			)

		if before.approval_status == "Approved" and any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in self.APPROVAL_SENSITIVE_FIELDS
		):
			self.approval_status = "Draft"
			self.submitted_by = None
			self.submitted_on = None
			self.approved_by = None
			self.approved_on = None

	def validate(self):
		self._validate_approval_transition()
		self._validate_dates()
		self._validate_quantities()
		self._validate_sourcing_fields()
		self._validate_price_breaks()
		self._normalize_single_preferred_supplier()

	def _validate_dates(self):
		if self.price_valid_from and self.price_valid_to and self.price_valid_from > self.price_valid_to:
			frappe.throw(_("Price Valid From cannot be after Price Valid To."))

	def _validate_quantities(self):
		if flt(self.min_order_qty) < 0:
			frappe.throw(_("Minimum Order Qty cannot be negative."))
		if cint(self.lead_time_days) < 0:
			frappe.throw(_("Lead Time (Days) cannot be negative."))

	def _validate_sourcing_fields(self):
		if cint(self.source_rank) <= 0:
			frappe.throw(_("Source Rank must be greater than 0."))
		if flt(self.allocation_pct) < 0 or flt(self.allocation_pct) > 100:
			frappe.throw(_("Allocation % must be between 0 and 100."))

	def _validate_price_breaks(self):
		seen = set()
		for row in self.get("price_breaks") or []:
			if flt(row.min_qty) < 0:
				frappe.throw(_("Row {0}: Min Qty cannot be negative.").format(row.idx))
			if row.max_qty is not None and row.max_qty != "" and flt(row.max_qty) < flt(row.min_qty):
				frappe.throw(_("Row {0}: Max Qty cannot be less than Min Qty.").format(row.idx))
			if flt(row.unit_price) < 0:
				frappe.throw(_("Row {0}: Unit Price cannot be negative.").format(row.idx))
			if row.valid_from and row.valid_to and row.valid_from > row.valid_to:
				frappe.throw(_("Row {0}: Valid From cannot be after Valid To.").format(row.idx))
			key = (flt(row.min_qty), flt(row.max_qty or -1), (row.uom or ""))
			if key in seen:
				frappe.throw(_("Row {0}: Duplicate price-break range for UOM.").format(row.idx))
			seen.add(key)

	def _normalize_single_preferred_supplier(self):
		if self.approval_status != "Approved" or not cint(self.preferred) or not cint(self.active):
			return

		filters = {
			"item_code": self.item_code,
			"preferred": 1,
			"active": 1,
			"name": ("!=", self.name),
		}
		if self.company:
			filters["company"] = self.company
		if self.purchase_org:
			filters["purchase_org"] = self.purchase_org

		for name in frappe.get_all("CH Vendor Info Record", filters=filters, pluck="name"):
			frappe.db.set_value("CH Vendor Info Record", name, "preferred", 0, update_modified=False)
