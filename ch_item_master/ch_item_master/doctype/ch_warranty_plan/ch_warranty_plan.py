# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CHWarrantyPlan(Document):
	def autoname(self):
		"""Auto-generate warranty_plan_id if not set."""
		if not self.warranty_plan_id:
			max_id = frappe.db.sql("SELECT IFNULL(MAX(warranty_plan_id), 0) FROM `tabCH Warranty Plan`")[0][0]
			self.warranty_plan_id = int(max_id) + 1

	def validate(self):
		self._validate_pricing()
		self._validate_service_item()
		self._validate_duration()
		self._validate_deductible()
		self._validate_unique_plan_per_company()

	def _validate_pricing(self):
		"""Ensure pricing fields are consistent."""
		if self.pricing_mode == "Fixed":
			if (self.price or 0) <= 0:
				frappe.throw(
					_("Standard Price must be greater than zero for Fixed pricing"),
					title=_("Invalid Price"),
				)
		elif self.pricing_mode == "Percentage of Device Price":
			if not self.percentage_value or self.percentage_value <= 0:
				frappe.throw(
					_("Percentage Value is required and must be > 0 "
					  "when Pricing Mode is 'Percentage of Device Price'"),
					title=_("Invalid Percentage"),
				)
			if self.percentage_value > 100:
				frappe.throw(
					_("Percentage Value cannot exceed 100%"),
					title=_("Invalid Percentage"),
				)

		# Cost-to-company sanity check
		if self.cost_to_company and self.price and self.pricing_mode == "Fixed":
			if self.cost_to_company > self.price:
				frappe.msgprint(
					_("Cost to Company ({0}) is higher than the selling Price ({1}). "
					  "This plan will generate a loss."
					).format(
						frappe.format_value(self.cost_to_company, {"fieldtype": "Currency"}),
						frappe.format_value(self.price, {"fieldtype": "Currency"}),
					),
					indicator="orange",
					title=_("Negative Margin"),
				)

	def _validate_service_item(self):
		"""Ensure the linked service item is a non-stock item."""
		if not self.service_item:
			return

		item = frappe.db.get_value(
			"Item", self.service_item, ["is_stock_item", "disabled"], as_dict=True
		)
		if not item:
			frappe.throw(
				_("Service Item {0} does not exist").format(frappe.bold(self.service_item)),
				title=_("Invalid Service Item"),
			)
			return

		if item.is_stock_item:
			frappe.throw(
				_("Service Item {0} is a stock item. Warranty/VAS plans must use "
				  "non-stock (service) items. Create a service item first."
				).format(frappe.bold(self.service_item)),
				title=_("Stock Item Not Allowed"),
			)

		if item.disabled:
			frappe.msgprint(
				_("Service Item {0} is disabled. Enable it before activating this plan."
				).format(frappe.bold(self.service_item)),
				indicator="orange",
				title=_("Disabled Service Item"),
			)

	def _validate_duration(self):
		"""Duration must be non-negative."""
		if (self.duration_months or 0) < 0:
			frappe.throw(
				_("Duration cannot be negative"),
				title=_("Invalid Duration"),
			)

	def _validate_deductible(self):
		"""Deductible amount must be non-negative and less than price."""
		if (self.deductible_amount or 0) < 0:
			frappe.throw(
				_("Deductible Amount cannot be negative"),
				title=_("Invalid Deductible"),
			)
		if self.deductible_amount and self.price and self.deductible_amount >= self.price:
			frappe.msgprint(
				_("Deductible Amount ({0}) is equal to or exceeds the plan Price ({1}). "
				  "Customers will pay full price on every claim."
				).format(
					frappe.format_value(self.deductible_amount, {"fieldtype": "Currency"}),
					frappe.format_value(self.price, {"fieldtype": "Currency"}),
				),
				indicator="orange",
				title=_("High Deductible"),
			)

	def _validate_unique_plan_per_company(self):
		"""Warn if the same plan_name exists for another company (might be intentional for multi-company)."""
		if not self.plan_name or not self.company:
			return
		existing = frappe.db.get_value(
			"CH Warranty Plan",
			{
				"plan_name": self.plan_name,
				"company": self.company,
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("A plan named {0} already exists for company {1} ({2}). "
				  "Use a different name or update the existing plan."
				).format(
					frappe.bold(self.plan_name),
					frappe.bold(self.company),
					existing,
				),
				title=_("Duplicate Plan Name"),
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
				frappe.db.set_value("Item Price", existing, {
					"price_list_rate": self.price,
					"company": self.company or "",
				})
			else:
				ip = frappe.new_doc("Item Price")
				ip.item_code = self.service_item
				ip.price_list = price_list
				ip.price_list_rate = self.price
				ip.selling = 1
				ip.company = self.company or ""
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

		# Company filter: match the specific company OR plans with no company set (global)
		if company:
			plans = frappe.get_all(
				"CH Warranty Plan",
				filters=filters,
				or_filters=[
					["company", "=", company],
					["company", "=", ""],
					["company", "is", "not set"],
				],
				fields=[
					"name", "plan_name", "plan_type", "service_item",
					"price", "pricing_mode", "percentage_value",
					"duration_months", "attach_level", "coverage_description",
				],
			)
		else:
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
