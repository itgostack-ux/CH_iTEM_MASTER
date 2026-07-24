# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from ch_item_master.config import get_int_setting
from ch_item_master.id_sequences import next_numeric_id


class CHWarrantyPlan(Document):
	def autoname(self):
		"""Auto-generate warranty_plan_id if not set."""
		if self.plan_name:
			self.plan_name = " ".join(self.plan_name.split())
		if not self.warranty_plan_id:
			self.warranty_plan_id = next_numeric_id("warranty_plan")

	def validate(self):
		if self.plan_name:
			self.plan_name = " ".join(self.plan_name.split())
		self._validate_pricing()
		self._validate_service_item()
		self._validate_duration()
		self._validate_deductible()
		self._validate_validity_dates()
		self._validate_external_device_settings()
		self._validate_benefit_rules()
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

	def _validate_validity_dates(self):
		"""Ensure valid_from <= valid_to when both are set."""
		if self.valid_from and self.valid_to:
			from frappe.utils import getdate
			if getdate(self.valid_from) > getdate(self.valid_to):
				frappe.throw(
					_("Valid From ({0}) cannot be after Valid To ({1})").format(
						frappe.format_value(self.valid_from, {"fieldtype": "Date"}),
						frappe.format_value(self.valid_to, {"fieldtype": "Date"}),
					),
					title=_("Invalid Validity Period"),
				)

	def _validate_benefit_rules(self):
		"""Benefit rows are optional, but when present they must be usable."""
		seen = set()
		for row in (self.benefit_rules or []):
			code = (row.benefit_code or "").strip()
			if code:
				if code in seen:
					frappe.throw(
						_("Benefit Code {0} is duplicated. Use stable unique codes per plan.").format(
							frappe.bold(code)
						),
						title=_("Duplicate Benefit Code"),
					)
				seen.add(code)
			if row.covered and not row.fulfillment_type:
				row.fulfillment_type = self.fulfillment_type or "Repair Claim"

	def _validate_external_device_settings(self):
		"""External IMEI plans are allowed when checkbox is enabled.
		
		The generic device item can be configured on the plan or defaults to
		a company-level setting. This allows simple configuration without
		complex category mappings.
		"""
		if not self.allow_external_device:
			return

		if self.plan_type not in ("Value Added Service", "Protection Plan"):
			frappe.throw(
				_("Customer-provided IMEI is only supported for VAS / Protection plans."),
				title=_("Invalid External Device Setup"),
			)

		# If a plan-specific generic device item is configured, validate it
		if self.external_device_item:
			item = frappe.db.get_value(
				"Item",
				self.external_device_item,
				["disabled", "is_stock_item"],
				as_dict=True,
			)
			if not item:
				frappe.throw(
					_("External Device Item {0} does not exist").format(
						frappe.bold(self.external_device_item)
					),
					title=_("Invalid External Device Item"),
				)
			if item.disabled:
				frappe.throw(
					_("External Device Item {0} is disabled").format(
						frappe.bold(self.external_device_item)
					),
					title=_("Disabled External Device Item"),
				)
			if item.is_stock_item:
				frappe.throw(
					_("External Device Item {0} must be a non-stock (service) item").format(
						frappe.bold(self.external_device_item)
					),
					title=_("Stock Item Not Allowed"),
				)
			if self.external_device_item == self.service_item:
				frappe.throw(
					_("External Device Item cannot be the same as the plan Service Item."),
					title=_("Invalid External Device Setup"),
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
				filters={"disabled": 0, "is_buying": 0},
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
				})
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
	def get_applicable_plans(item_code=None, item_group=None, channel=None,
	                         company=None, brand=None) -> dict:
		"""Return warranty/VAS plans applicable to a given item and channel.

		Used by POS and transaction UI to suggest add-on plans.
		Filters by: status, company, brand, valid_from/valid_to, item_group, channel.
		"""
		from frappe.utils import nowdate, getdate

		filters = {"status": "Active"}
		today = getdate(nowdate())
		candidate_limit = min(
			get_int_setting("warranty_plan_candidate_limit", 500, minimum=1),
			2000,
		)
		result_limit = min(
			get_int_setting("warranty_plan_result_limit", 50, minimum=1),
			candidate_limit,
		)
		fields = [
			"name", "plan_name", "plan_type", "service_item",
			"price", "pricing_mode", "percentage_value",
			"duration_months", "attach_level", "coverage_description",
			"brand", "valid_from", "valid_to", "max_claims",
			"deductible_amount", "priority", "requires_approval",
			"company_share_percent", "coverage_type_override",
			"fulfillment_type",
		]

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
				fields=fields,
				order_by="priority desc, name asc",
				limit_page_length=candidate_limit,
			)
		else:
			plans = frappe.get_all(
				"CH Warranty Plan",
				filters=filters,
				fields=fields,
				order_by="priority desc, name asc",
				limit_page_length=candidate_limit,
			)

		plan_names = tuple(plan.name for plan in plans)
		group_match = {}
		if item_group and plan_names:
			group_match = {
				row.parent: bool(row.matches)
				for row in frappe.db.sql(
					"""
						SELECT parent, MAX(item_group = %(item_group)s) AS matches
						FROM `tabCH Warranty Plan Item Group`
						WHERE parent IN %(plans)s
						GROUP BY parent
					""",
					{"item_group": item_group, "plans": plan_names},
					as_dict=True,
				)
			}
		channel_match = {}
		if channel and plan_names:
			channel_match = {
				row.parent: bool(row.matches)
				for row in frappe.db.sql(
					"""
						SELECT parent, MAX(channel = %(channel)s) AS matches
						FROM `tabCH Warranty Plan Channel`
						WHERE parent IN %(plans)s
						GROUP BY parent
					""",
					{"channel": channel, "plans": plan_names},
					as_dict=True,
				)
			}

		result = []
		for plan in plans:
			# Check validity dates (promotional window)
			if plan.valid_from and today < getdate(plan.valid_from):
				continue
			if plan.valid_to and today > getdate(plan.valid_to):
				continue

			# Check brand applicability
			if brand and plan.brand and plan.brand != brand:
				continue

			# Check item group applicability
			if item_group and plan.name in group_match and not group_match[plan.name]:
				continue

			# Check channel applicability
			if channel and plan.name in channel_match and not channel_match[plan.name]:
				continue

			result.append(plan)
			if len(result) >= result_limit:
				break

		return result
