# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, cint
from frappe.model.document import Document


class CHVendorInfoRecord(Document):
	def validate(self):
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
		if not cint(self.preferred) or not cint(self.active):
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
