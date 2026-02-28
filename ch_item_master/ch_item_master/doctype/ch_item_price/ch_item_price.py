# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from ch_item_master.ch_item_master.exceptions import (
	InvalidPriceError,
	InvalidPriceHierarchyError,
	OverlappingPriceError,
)


class CHItemPrice(Document):
	def validate(self):
		self._validate_positive_prices()
		self._validate_price_hierarchy()
		self._validate_effective_dates()
		self._validate_channel_active()
		self._check_overlapping_price()
		self._auto_set_status()

	def _validate_channel_active(self):
		"""Warn if the price channel is inactive."""
		if not self.channel:
			return
		is_active = frappe.db.get_value("CH Price Channel", self.channel, "is_active")
		if not is_active:
			frappe.msgprint(
				_("Channel {0} is currently inactive. This price will not apply "
				  "to any transactions until the channel is reactivated."
				).format(frappe.bold(self.channel)),
				indicator="orange",
				title=_("Inactive Channel"),
			)

	def _validate_positive_prices(self):
		"""All price fields must be non-negative; selling_price must be > 0."""
		for field, label in [("mrp", "MRP"), ("mop", "MOP"), ("selling_price", "Selling Price")]:
			val = self.get(field) or 0
			if val < 0:
				frappe.throw(
					_("{0} cannot be negative ({1})").format(label, val),
					title=_("Invalid Price"),
					exc=InvalidPriceError,
				)
		if (self.selling_price or 0) <= 0:
			frappe.throw(
				_("Selling Price must be greater than zero"),
				title=_("Invalid Price"),
				exc=InvalidPriceError,
			)

	def _validate_price_hierarchy(self):
		"""MRP >= MOP >= Selling Price (when all three are provided)."""
		mrp = self.mrp or 0
		mop = self.mop or 0
		sp  = self.selling_price or 0

		if mrp and mop and mrp < mop:
			frappe.throw(
				_("MRP ({0}) cannot be less than MOP ({1})").format(mrp, mop),
				title=_("Invalid Price Hierarchy"),
				exc=InvalidPriceHierarchyError,
			)
		if mop and sp and mop < sp:
			frappe.throw(
				_("MOP ({0}) cannot be less than Selling Price ({1})").format(mop, sp),
				title=_("Invalid Price Hierarchy"),
				exc=InvalidPriceHierarchyError,
			)
		if mrp and sp and mrp < sp:
			frappe.throw(
				_("MRP ({0}) cannot be less than Selling Price ({1})").format(mrp, sp),
				title=_("Invalid Price Hierarchy"),
				exc=InvalidPriceHierarchyError,
			)

	def _validate_effective_dates(self):
		"""Effective To must be >= Effective From."""
		if self.effective_to and self.effective_from:
			if getdate(self.effective_to) < getdate(self.effective_from):
				frappe.throw(
					_("Effective To ({0}) cannot be before Effective From ({1})").format(
						self.effective_to, self.effective_from
					),
					title=_("Invalid Date Range"),
				)

	def _check_overlapping_price(self):
		"""No two active price records for the same Item + Channel + Company should overlap."""
		from_date = getdate(self.effective_from)
		to_date   = getdate(self.effective_to) if self.effective_to else None

		# Use database-level locking to prevent race conditions
		# This ensures two concurrent saves don't both pass validation
		if not self.is_new():
			frappe.db.sql(
				"""
				SELECT name FROM `tabCH Item Price`
				WHERE item_code = %s AND channel = %s AND company = %s AND name != %s
				FOR UPDATE
				""",
				(self.item_code, self.channel, self.company, self.name or ""),
			)

		filters = {
			"item_code": self.item_code,
			"channel": self.channel,
			"company": self.company,
			"name": ("!=", self.name),
			"status": ("in", ["Active", "Scheduled"]),
		}
		# Pre-filter in SQL: only fetch records whose start is before our end date
		if to_date:
			filters["effective_from"] = ("<=", str(to_date))

		# Records that end on or after our start, OR have no end date (open-ended)
		existing = frappe.get_all(
			"CH Item Price",
			filters=filters,
			or_filters=[
				["effective_to", "is", "not set"],
				["effective_to", ">=", str(from_date)],
			],
			fields=["name", "effective_from", "effective_to"],
		)

		conflicts = []
		for ex in existing:
			ex_from = getdate(ex.effective_from)
			ex_to   = getdate(ex.effective_to) if ex.effective_to else None

			# Overlap when: NOT (to_date < ex_from OR from_date > ex_to)
			no_overlap = (
				(to_date and to_date < ex_from)
				or (ex_to and from_date > ex_to)
			)
			if not no_overlap:
				conflicts.append(ex.name)

		if conflicts:
			frappe.throw(
				_(
					"Overlapping price record(s) found for Item <b>{0}</b>, "
					"Channel <b>{1}</b>: {2}. "
					"Set Effective To on existing records or change dates."
				).format(
					self.item_code,
					self.channel,
					", ".join(conflicts),
				),
				title=_("Overlapping Price Records"),
				exc=OverlappingPriceError,
			)

	def _auto_set_status(self):
		"""Auto-compute status based on effective dates and approval."""
		today = getdate(nowdate())
		from_date = getdate(self.effective_from)
		to_date   = getdate(self.effective_to) if self.effective_to else None

		# Draft stays Draft until explicitly approved
		if self.status == "Draft":
			return

		if to_date and today > to_date:
			self.status = "Expired"
		elif today < from_date:
			self.status = "Scheduled"
		else:
			self.status = "Active"

	def on_update(self):
		"""Sync selling price to ERPNext native Item Price so all transactions auto-pick it up.

		Only sync when approved (status is Active/Scheduled). Draft prices don't sync.
		"""
		if self.status in ("Active", "Scheduled"):
			self._sync_to_erp_item_price()
		elif self.status == "Expired":
			self._expire_erp_item_price()

	@frappe.whitelist()
	def approve(self):
		"""Approve this price record — activates it and syncs to ERPNext.

		Only CH Price Manager or System Manager can approve.
		"""
		from frappe.utils import now_datetime as _now
		frappe.only_for(["System Manager", "CH Price Manager", "CH Master Manager"])

		self.approved_by = frappe.session.user
		self.approved_at = _now()
		# Compute effective status
		today = getdate(nowdate())
		from_date = getdate(self.effective_from)
		to_date = getdate(self.effective_to) if self.effective_to else None

		if to_date and today > to_date:
			self.status = "Expired"
		elif today < from_date:
			self.status = "Scheduled"
		else:
			self.status = "Active"

		self.save()

		frappe.msgprint(
			_("{0} approved — status set to {1}, synced to ERPNext Item Price").format(
				self.name, frappe.bold(self.status)
			),
			indicator="green",
		)

	@frappe.whitelist()
	def reject(self):
		"""Reject this price record — expires it and disables the ERPNext sync."""
		frappe.only_for(["System Manager", "CH Price Manager", "CH Master Manager"])

		self.status = "Expired"
		self.save()

		# Expire the linked ERPNext Item Price
		self._expire_erp_item_price()

		frappe.msgprint(
			_("{0} rejected — status set to Expired").format(self.name),
			indicator="orange",
		)

	def _sync_to_erp_item_price(self):
		"""Create or update an ERPNext Item Price record.

		ERPNext natively reads Item Price in all selling/buying transactions so
		syncing here removes the need for a custom apply_ch_pricing hook.
		Handles both selling channels (POS/Website/App/Marketplace) and buying
		channels (Buyback) by checking is_buying on the CH Price Channel.
		"""
		price_list = self._get_price_list()
		if not price_list:
			# Log warning instead of silently failing
			frappe.log_error(
				f"CH Price Channel '{self.channel}' has no linked Price List. "
				f"Cannot sync CH Item Price {self.name} to ERPNext Item Price.",
				"CH Item Price Sync Warning"
			)
			return

		is_buying = frappe.db.get_value("CH Price Channel", self.channel, "is_buying") or 0

		existing = frappe.db.get_value(
			"Item Price",
			{"item_code": self.item_code, "price_list": price_list, "ch_source_price": self.name},
			"name",
		)

		if existing:
			ip = frappe.get_doc("Item Price", existing)
		else:
			ip = frappe.new_doc("Item Price")
			ip.item_code  = self.item_code
			ip.price_list = price_list
			ip.selling    = 0 if is_buying else 1
			ip.buying     = 1 if is_buying else 0
			ip.currency   = frappe.get_value("Price List", price_list, "currency") or "INR"

		ip.price_list_rate = self.selling_price
		ip.ch_mrp          = self.mrp
		ip.ch_mop          = self.mop
		ip.valid_from      = self.effective_from
		ip.valid_upto      = self.effective_to or None
		ip.ch_source_price = self.name
		ip.company         = self.company or ""
		ip.note = f"Synced from CH Item Price {self.name}"

		ip.flags.ignore_permissions = True
		ip.flags.ignore_validate_update_after_submit = True
		ip.save()

		# Store back-reference (without retriggering on_update)
		frappe.db.set_value("CH Item Price", self.name, "erp_item_price", ip.name, update_modified=False)

	def _expire_erp_item_price(self):
		"""Set valid_upto = today on the linked ERPNext Item Price."""
		price_list = self._get_price_list()
		if not price_list:
			return
		existing = frappe.db.get_value(
			"Item Price",
			{"item_code": self.item_code, "price_list": price_list, "ch_source_price": self.name},
			"name",
		)
		if existing:
			from frappe.utils import today
			frappe.db.set_value("Item Price", existing, "valid_upto", today(), update_modified=False)

	def _get_price_list(self):
		"""Resolve the ERPNext Price List name from the linked CH Price Channel."""
		return frappe.db.get_value("CH Price Channel", self.channel, "price_list")
