# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Sold Plan — tracks warranty/VAS plans issued to customers per device.

Lifecycle:
  Active → Expired (automatic, via scheduled task)
  Active → Claimed (when max_claims reached)
  Active → Void (manual, by warranty manager)
  Any → Cancelled (via amend/cancel)
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, getdate, add_months

from ch_item_master.ch_item_master.exceptions import (
	WarrantyExpiredError,
	MaxClaimsReachedError,
	DuplicateSoldPlanError,
)


class CHSoldPlan(Document):
	def autoname(self):
		"""Auto-generate sold_plan_id if not set."""
		if not self.sold_plan_id:
			max_id = frappe.db.sql(
				"SELECT IFNULL(MAX(sold_plan_id), 0) FROM `tabCH Sold Plan`"
			)[0][0]
			self.sold_plan_id = int(max_id) + 1

	def validate(self):
		self._validate_dates()
		self._validate_plan_active()
		self._validate_duplicate()
		self._auto_compute_end_date()
		self._set_sold_by()

	def on_submit(self):
		self._sync_to_serial_lifecycle()

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")
		self._clear_serial_lifecycle()

	def _validate_dates(self):
		"""Start date must be before end date."""
		if self.start_date and self.end_date:
			if getdate(self.start_date) > getdate(self.end_date):
				frappe.throw(
					_("Start Date ({0}) cannot be after End Date ({1})").format(
						self.start_date, self.end_date
					),
					title=_("Invalid Coverage Period"),
				)

	def _validate_plan_active(self):
		"""Ensure the linked warranty plan is Active."""
		if not self.warranty_plan:
			return
		plan_status = frappe.db.get_value("CH Warranty Plan", self.warranty_plan, "status")
		if plan_status != "Active":
			frappe.throw(
				_("Warranty Plan {0} is {1}. Only Active plans can be issued.").format(
					frappe.bold(self.warranty_plan), plan_status
				),
				title=_("Inactive Plan"),
			)

	def _validate_duplicate(self):
		"""Prevent duplicate active sold plans for the same serial + plan type."""
		if not self.serial_no or not self.warranty_plan:
			return

		plan_type = frappe.db.get_value("CH Warranty Plan", self.warranty_plan, "plan_type")

		existing = frappe.db.get_value(
			"CH Sold Plan",
			{
				"serial_no": self.serial_no,
				"plan_type": plan_type,
				"status": "Active",
				"docstatus": 1,
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("Serial {0} already has an active {1} plan ({2}). "
				  "Void or cancel the existing plan first.").format(
					frappe.bold(self.serial_no),
					frappe.bold(plan_type),
					existing,
				),
				exc=DuplicateSoldPlanError,
				title=_("Duplicate Plan"),
			)

	def _auto_compute_end_date(self):
		"""Auto-calculate end_date from start_date + plan duration if not set."""
		if self.start_date and not self.end_date and self.warranty_plan:
			duration = frappe.db.get_value(
				"CH Warranty Plan", self.warranty_plan, "duration_months"
			)
			if duration:
				self.end_date = add_months(self.start_date, duration)

	def _set_sold_by(self):
		"""Set sold_by to current user if not already set."""
		if not self.sold_by:
			self.sold_by = frappe.session.user

	def _sync_to_serial_lifecycle(self):
		"""Update CH Serial Lifecycle with warranty info on submit."""
		if not self.serial_no:
			return

		if not frappe.db.exists("CH Serial Lifecycle", self.serial_no):
			return

		lc = frappe.get_doc("CH Serial Lifecycle", self.serial_no)

		plan_type = self.plan_type or frappe.db.get_value(
			"CH Warranty Plan", self.warranty_plan, "plan_type"
		)

		# For "Own Warranty" — set primary warranty fields
		# For "Extended Warranty" — set extended_warranty_end
		# For "VAS"/"Protection Plan" — don't overwrite base warranty
		if plan_type == "Own Warranty":
			lc.warranty_plan = self.warranty_plan
			lc.warranty_start_date = self.start_date
			lc.warranty_end_date = self.end_date
			lc.warranty_status = "Under Warranty"
		elif plan_type == "Extended Warranty":
			lc.extended_warranty_end = self.end_date
			lc.warranty_status = "Extended"
			# Also update warranty_plan if no base plan was set
			if not lc.warranty_plan:
				lc.warranty_plan = self.warranty_plan

		lc.flags.ignore_permissions = True
		lc.save()

	def _clear_serial_lifecycle(self):
		"""Revert CH Serial Lifecycle warranty on cancel (only if it points to this plan)."""
		if not self.serial_no:
			return

		if not frappe.db.exists("CH Serial Lifecycle", self.serial_no):
			return

		lc = frappe.get_doc("CH Serial Lifecycle", self.serial_no)

		if lc.warranty_plan == self.warranty_plan:
			lc.warranty_plan = None
			lc.warranty_start_date = None
			lc.warranty_end_date = None
			lc.warranty_status = ""
			lc.flags.ignore_permissions = True
			lc.save()

	# ── Public methods ───────────────────────────────────────────────────────

	def record_claim(self, service_reference=None):
		"""Increment claims_used. Called by GoFix when a service order is created under warranty.

		Args:
			service_reference: Optional reference to the service document.

		Raises:
			WarrantyExpiredError: If plan has expired.
			MaxClaimsReachedError: If max_claims limit reached.
		"""
		today = getdate(nowdate())

		if self.end_date and today > getdate(self.end_date):
			self.db_set("status", "Expired")
			frappe.throw(
				_("Warranty plan {0} expired on {1}").format(
					frappe.bold(self.plan_title or self.warranty_plan),
					self.end_date,
				),
				exc=WarrantyExpiredError,
				title=_("Warranty Expired"),
			)

		if self.max_claims and self.max_claims > 0:
			if (self.claims_used or 0) >= self.max_claims:
				frappe.throw(
					_("Maximum claims ({0}) reached for plan {1}").format(
						self.max_claims,
						frappe.bold(self.plan_title or self.warranty_plan),
					),
					exc=MaxClaimsReachedError,
					title=_("Claims Exhausted"),
				)

		self.claims_used = (self.claims_used or 0) + 1
		self.db_set("claims_used", self.claims_used)

		# Check if claims now exhausted
		if self.max_claims and self.max_claims > 0 and self.claims_used >= self.max_claims:
			self.db_set("status", "Claimed")

		frappe.msgprint(
			_("Warranty claim recorded. Claims used: {0}/{1}").format(
				self.claims_used,
				self.max_claims or _("Unlimited"),
			),
			indicator="green",
		)


# ── Whitelisted API ────────────────────────────────────────────────────────

@frappe.whitelist()
def get_active_plans_for_serial(serial_no, company=None):
	"""Get all active sold plans for a serial number.

	Used by GoFix Service Request to determine warranty coverage.

	Returns:
		list[dict]: Active sold plans with plan details.
	"""
	filters = {
		"serial_no": serial_no,
		"status": "Active",
		"docstatus": 1,
	}
	if company:
		filters["company"] = company

	plans = frappe.get_all(
		"CH Sold Plan",
		filters=filters,
		fields=[
			"name", "warranty_plan", "plan_title", "plan_type",
			"start_date", "end_date", "claims_used", "max_claims",
			"deductible_amount", "customer", "customer_name",
			"item_code", "item_name", "brand",
		],
		order_by="end_date desc",
	)

	# Annotate with validity status
	today = getdate(nowdate())
	for plan in plans:
		if plan.end_date and today > getdate(plan.end_date):
			plan["is_valid"] = False
			plan["validity_note"] = "Expired"
		elif plan.max_claims and plan.max_claims > 0 and (plan.claims_used or 0) >= plan.max_claims:
			plan["is_valid"] = False
			plan["validity_note"] = "Max claims reached"
		else:
			plan["is_valid"] = True
			plan["validity_note"] = "Valid"

	return plans


@frappe.whitelist()
def check_warranty_status(serial_no, company=None):
	"""Quick warranty check for a serial number.

	Returns a summary: warranty_covered (bool), covering_plan (dict or None),
	all_plans (list), warranty_status (str).
	"""
	plans = get_active_plans_for_serial(serial_no, company)

	valid_plans = [p for p in plans if p.get("is_valid")]

	if not valid_plans:
		return {
			"warranty_covered": False,
			"warranty_status": "Out of Warranty" if plans else "No Warranty",
			"covering_plan": None,
			"all_plans": plans,
		}

	# Prefer "Own Warranty" > "Extended Warranty" > others
	priority = {"Own Warranty": 1, "Extended Warranty": 2, "Value Added Service": 3, "Protection Plan": 4}
	valid_plans.sort(key=lambda p: priority.get(p.get("plan_type"), 99))

	return {
		"warranty_covered": True,
		"warranty_status": "Under Warranty",
		"covering_plan": valid_plans[0],
		"all_plans": plans,
	}
