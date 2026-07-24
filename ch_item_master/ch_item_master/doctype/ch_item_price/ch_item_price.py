# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from ch_item_master.security import require_scoped_document_action
from ch_item_master.ch_item_master.exceptions import (
	InvalidPriceError,
	InvalidPriceHierarchyError,
	OverlappingPriceError,
)


class CHItemPrice(Document):
	_APPROVAL_CONTEXT = object()
	_PROTECTED_FIELDS = ("status", "approved_by", "approved_at", "erp_item_price")
	_APPROVAL_SENSITIVE_FIELDS = (
		"item_code",
		"channel",
		"company",
		"mrp",
		"mop",
		"selling_price",
		"cost_price",
		"effective_from",
		"effective_to",
	)

	def _authorize_approval_transition(self):
		self.flags.ch_item_price_approval_context = self._APPROVAL_CONTEXT

	def _has_approval_context(self):
		return self.flags.get("ch_item_price_approval_context") is self._APPROVAL_CONTEXT

	def _require_action(self, action):
		require_scoped_document_action(
			self,
			"price_approval_roles",
			("System Manager", "CH Master Approver", "CH Master Manager", "CH Price Manager"),
			action=action,
			permission_types=("write",),
			lock=True,
		)

	def _validate_approval_transition(self):
		if self._has_approval_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			for fieldname in self._PROTECTED_FIELDS:
				value = self.get(fieldname)
				allowed = (None, "", "Draft") if fieldname == "status" else (None, "")
				if value not in allowed:
					frappe.throw(
						_("{0} is set only by the item-price approval workflow.").format(
							self.meta.get_label(fieldname) or fieldname
						),
						frappe.PermissionError,
					)
			return

		if any(self.get(fieldname) != before.get(fieldname) for fieldname in self._PROTECTED_FIELDS):
			frappe.throw(
				_("Item-price approval state can only be changed through Approve or Reject."),
				frappe.PermissionError,
			)

		if before.status in ("Active", "Scheduled") and any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in self._APPROVAL_SENSITIVE_FIELDS
		):
			self.status = "Draft"
			self.approved_by = None
			self.approved_at = None

	def validate(self):
		self._validate_approval_transition()
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
		disabled = frappe.db.get_value("CH Price Channel", self.channel, "disabled")
		if disabled:
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
		"""MRP >= Selling Price (when provided).

		TC_019: MOP is allowed to be below Selling Price, so the MOP >= Selling
		Price constraint is intentionally not enforced. MRP must still be the
		ceiling for both MOP and Selling Price.
		"""
		mrp = self.mrp or 0
		mop = self.mop or 0
		sp  = self.selling_price or 0

		if mrp and mop and mrp < mop:
			frappe.throw(
				_("MRP ({0}) cannot be less than MOP ({1})").format(mrp, mop),
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

		# IM-3 fix: Use database-level locking to prevent race conditions
		# Lock for BOTH new and existing docs so concurrent creates can't bypass overlap check
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
		Also syncs mrp back to Item.ch_item_mrp so the item card stays current.
		"""
		if self.status in ("Active", "Scheduled"):
			self._sync_to_erp_item_price()
			# Push MRP back to Item so Item.ch_item_mrp is always current.
			from ch_item_master.ch_item_master.item_mrp import sync_price_mrp_to_item
			sync_price_mrp_to_item(self)
		elif self.status == "Expired":
			self._expire_erp_item_price()
		elif self.status == "Draft" and self.get("erp_item_price"):
			self._expire_erp_item_price()

	@frappe.whitelist(methods=["POST"])
	def approve(self) -> None:
		"""Approve this price record — activates it and syncs to ERPNext.

		Only CH Price Manager or System Manager can approve.
		"""
		from frappe.utils import now_datetime as _now
		self._require_action(_("approve an item price"))
		self._authorize_approval_transition()

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

	@frappe.whitelist(methods=["POST"])
	def reject(self) -> None:
		"""Reject this price record — expires it and disables the ERPNext sync."""
		self._require_action(_("reject an item price"))
		self._authorize_approval_transition()

		self.status = "Expired"
		self.approved_by = None
		self.approved_at = None
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
