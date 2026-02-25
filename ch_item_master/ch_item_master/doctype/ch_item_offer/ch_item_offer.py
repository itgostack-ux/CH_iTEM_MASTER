# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class CHItemOffer(Document):
	def validate(self):
		self._validate_dates()
		self._validate_value()
		self._validate_targets()
		self._auto_set_status()

	def _validate_targets(self):
		"""Ensure correct targeting fields are filled based on offer_level and apply_on."""
		offer_level = self.get("offer_level") or "Item"
		apply_on = self.get("apply_on") or "Item Code"

		if offer_level == "Item":
			if apply_on == "Item Code" and not self.item_code:
				frappe.throw(_("Item Code is required for item-level offers with Apply On = Item Code"))
			elif apply_on == "Item Group" and not self.get("target_item_group"):
				frappe.throw(_("Target Item Group is required when Apply On = Item Group"))
			elif apply_on == "Brand" and not self.get("target_brand"):
				frappe.throw(_("Target Brand is required when Apply On = Brand"))
		# Bill-level offers don't require item_code

	def _validate_dates(self):
		if self.start_date and self.end_date:
			if get_datetime(self.end_date) <= get_datetime(self.start_date):
				frappe.throw(
					_("End Date must be after Start Date"),
					title=_("Invalid Dates"),
				)

	def _validate_value(self):
		if (self.value or 0) <= 0:
			frappe.throw(
				_("Offer value must be greater than zero"),
				title=_("Invalid Value"),
			)
		if self.value_type == "Percentage" and self.value > 100:
			frappe.throw(
				_("Percentage discount cannot exceed 100%"),
				title=_("Invalid Value"),
			)
		# Validate amount discounts aren't unreasonably high
		if self.value_type == "Amount":
			# Get max discount from settings, default to 100,000
			max_discount = float(frappe.db.get_single_value("System Settings", "ch_max_discount_amount") or 100000)
			if self.value > max_discount:
				frappe.throw(
					_("Discount amount {0} exceeds maximum allowed {1}").format(
						frappe.format_value(self.value, dict(fieldtype="Currency")),
						frappe.format_value(max_discount, dict(fieldtype="Currency"))
					),
					title=_("Invalid Discount Amount"),
				)
		if now > end:
			self.status = "Expired"
		elif now < start:
			self.status = "Scheduled"
		else:
			self.status = "Active"

	@frappe.whitelist()
	def approve(self):
		"""Approve this offer — called from form action button."""
		frappe.only_for(["System Manager", "CH Master Manager"])
		self.approval_status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_at = now_datetime()
		self._auto_set_status()
		self.save()
		self._sync_to_erp_pricing_rule()
		frappe.msgprint(
			_("{0} approved — Pricing Rule created/updated in ERPNext").format(self.name),
			indicator="green",
		)

	@frappe.whitelist()
	def reject(self):
		"""Reject this offer."""
		frappe.only_for(["System Manager", "CH Master Manager"])
		self.approval_status = "Rejected"
		self.status = "Cancelled"
		# Disable the linked Pricing Rule if it exists
		if self.get("erp_pricing_rule") and frappe.db.exists("Pricing Rule", self.erp_pricing_rule):
			frappe.db.set_value("Pricing Rule", self.erp_pricing_rule, "disable", 1)
		self.save()
		frappe.msgprint(_("{0} rejected").format(self.name), indicator="orange")

	def _sync_to_erp_pricing_rule(self):
		"""Create or update an ERPNext Pricing Rule from this approved offer.

		ERPNext Pricing Rules are auto-applied to all selling transactions
		(Sales Invoice, Sales Order, POS, Quotation, Delivery Note) — no hook needed.

		Supports:
		- Item-level offers: apply_on = Item Code / Item Group / Brand
		- Bill-level offers: apply_on = Transaction, apply_discount_on = Grand Total / Net Total
		"""
		# Map offer value type → ERPNext rate_or_discount
		if self.value_type == "Amount":
			rate_or_discount = "Discount Amount"
		elif self.value_type == "Price Override":
			rate_or_discount = "Rate"
		else:
			rate_or_discount = "Discount Percentage"

		price_list = None
		if self.channel:
			price_list = frappe.db.get_value("CH Price Channel", self.channel, "price_list")

		erp_pr = self.get("erp_pricing_rule")
		if erp_pr and frappe.db.exists("Pricing Rule", erp_pr):
			pr = frappe.get_doc("Pricing Rule", erp_pr)
			# Clear existing child tables so they are rebuilt
			pr.items = []
			pr.item_groups = []
			pr.brands = []
		else:
			pr = frappe.new_doc("Pricing Rule")
			pr.selling  = 1
			pr.price_or_product_discount = "Price"

		offer_level = self.get("offer_level") or "Item"
		apply_on = self.get("apply_on") or "Item Code"

		if offer_level == "Bill":
			# Bill-level / Transaction-level offer
			pr.apply_on = "Transaction"
			pr.apply_discount_on = self.get("apply_discount_on") or "Grand Total"
		elif apply_on == "Item Group":
			pr.apply_on = "Item Group"
			pr.append("item_groups", {"item_group": self.get("target_item_group")})
		elif apply_on == "Brand":
			pr.apply_on = "Brand"
			pr.append("brands", {"brand": self.get("target_brand")})
		else:
			pr.apply_on = "Item Code"
			pr.append("items", {"item_code": self.item_code})

		pr.title             = self.offer_name or self.name
		pr.company           = self.company or ""
		pr.disable           = 0
		pr.rate_or_discount  = rate_or_discount
		pr.valid_from        = self.start_date
		pr.valid_upto        = self.end_date
		pr.priority          = int(self.priority or 1)
		pr.for_price_list    = price_list
		pr.min_amt           = self.min_bill_amount or 0
		pr.rule_description  = (
			f"CH Offer: {self.offer_name} | Type: {self.offer_type} | "
			f"Level: {offer_level} | "
			+ (f"Bank: {self.bank_name} {self.card_type or ''}" if self.bank_name else "")
		)

		if rate_or_discount == "Discount Percentage":
			pr.discount_percentage = self.value
		elif rate_or_discount == "Discount Amount":
			pr.discount_amount = self.value
		else:
			pr.rate = self.value

		pr.flags.ignore_permissions = True
		pr.flags.ignore_validate_update_after_submit = True
		pr.save()

		frappe.db.set_value(
			"CH Item Offer", self.name, "erp_pricing_rule", pr.name, update_modified=False
		)
