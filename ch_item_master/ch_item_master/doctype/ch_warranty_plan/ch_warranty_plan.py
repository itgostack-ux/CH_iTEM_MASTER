# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHWarrantyPlan(Document):
	def validate(self):
		self._validate_pricing()
		self._validate_service_item()

	def _validate_pricing(self):
		"""Ensure pricing fields are consistent."""
		if (self.price or 0) <= 0 and self.pricing_mode == "Fixed":
			frappe.throw(_("Standard Price must be greater than zero for Fixed pricing"))
		if self.pricing_mode == "Percentage of Device Price":
			if not self.percentage_value or self.percentage_value <= 0:
				frappe.throw(_("Percentage Value is required when Pricing Mode is 'Percentage of Device Price'"))
			if self.percentage_value > 100:
				frappe.throw(_("Percentage Value cannot exceed 100%"))

	def _validate_service_item(self):
		"""Ensure the linked service item is a non-stock item."""
		if self.service_item:
			is_stock = frappe.db.get_value("Item", self.service_item, "is_stock_item")
			if is_stock:
				frappe.msgprint(
					_("Service Item {0} is a stock item. Warranty/VAS plans typically "
					  "use non-stock (service) items.").format(self.service_item),
					indicator="orange",
					title=_("Warning"),
				)

	def on_update(self):
		"""Sync plan price to ERPNext Item Price for applicable channels."""
		if self.status == "Active" and self.service_item and self.pricing_mode == "Fixed":
			self._sync_plan_prices()

	def _sync_plan_prices(self):
		"""Create/update ERPNext Item Price for the service_item in applicable channels.

		If applicable_channels is set, only sync to those channels.
		If empty, sync to all active selling channels.
		"""
		if not self.price:
			return

		# Determine target channels
		if self.applicable_channels:
			channel_names = [row.channel for row in self.applicable_channels]
		else:
			channel_names = frappe.get_all(
				"CH Price Channel",
				filters={"is_active": 1, "is_buying": 0},
				pluck="name",
			)

		for ch_name in channel_names:
			price_list = frappe.db.get_value("CH Price Channel", ch_name, "price_list")
			if not price_list:
				continue

			existing = frappe.db.get_value(
				"Item Price",
				{"item_code": self.service_item, "price_list": price_list},
				"name",
			)

			if existing:
				frappe.db.set_value("Item Price", existing, "price_list_rate", self.price)
			else:
				ip = frappe.new_doc("Item Price")
				ip.item_code = self.service_item
				ip.price_list = price_list
				ip.price_list_rate = self.price
				ip.selling = 1
				ip.currency = frappe.get_value("Price List", price_list, "currency") or "INR"
				ip.note = f"Warranty Plan: {self.plan_name}"
				ip.flags.ignore_permissions = True
				ip.save()

	@staticmethod
	@frappe.whitelist()
	def get_applicable_plans(item_code=None, item_group=None, channel=None, company=None):
		"""Return warranty/VAS plans applicable to a given item and channel.

		Used by POS and transaction UI to suggest add-on plans.
		"""
		filters = {"status": "Active"}
		if company:
			filters["company"] = ["in", [company, "", None]]

		plans = frappe.get_all(
			"CH Warranty Plan",
			filters=filters,
			fields=[
				"name", "plan_name", "plan_type", "service_item",
				"price", "pricing_mode", "percentage_value",
				"duration_months", "attach_level", "coverage_description",
			],
		)

		result = []
		for plan in plans:
			# Check item group applicability
			if item_group:
				applicable_groups = frappe.get_all(
					"CH Warranty Plan Item Group",
					filters={"parent": plan.name},
					pluck="item_group",
				)
				if applicable_groups and item_group not in applicable_groups:
					continue

			# Check channel applicability
			if channel:
				applicable_channels = frappe.get_all(
					"CH Warranty Plan Channel",
					filters={"parent": plan.name},
					pluck="channel",
				)
				if applicable_channels and channel not in applicable_channels:
					continue

			result.append(plan)

		return result
