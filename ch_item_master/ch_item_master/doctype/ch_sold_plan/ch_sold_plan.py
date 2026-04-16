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
from frappe.utils import nowdate, getdate, add_months, flt

from ch_item_master.ch_item_master.utils import validate_indian_phone

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
		if self.customer_phone:
			self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
		self._validate_dates()
		self._validate_plan_active()
		self._validate_category()
		self._validate_duplicate()
		self._auto_compute_end_date()
		self._set_sold_by()

	def on_submit(self):
		self._sync_to_serial_lifecycle()
		self._send_welcome_notification()
		self._log_vas_event("Plan Activated")

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")
		self._clear_serial_lifecycle()
		self._log_vas_event("Plan Cancelled")

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

	def _validate_category(self):
		"""Ensure device category matches plan's applicable categories."""
		if not self.warranty_plan or not self.item_code:
			return
		plan_categories = frappe.get_all(
			"CH Warranty Plan Category",
			filters={"parent": self.warranty_plan},
			pluck="category",
		)
		if not plan_categories:
			return  # no restriction
		device_category = frappe.db.get_value("Item", self.item_code, "ch_category")
		if not device_category:
			frappe.throw(
				_("Device {0} has no category set — cannot verify VAS eligibility.").format(
					frappe.bold(self.item_code)),
				title=_("Missing Category"),
			)
		if device_category not in plan_categories:
			frappe.throw(
				_("Plan {0} is restricted to {1}, but device {2} belongs to {3}.").format(
					frappe.bold(self.warranty_plan),
					frappe.bold(", ".join(plan_categories)),
					frappe.bold(self.item_code),
					frappe.bold(device_category),
				),
				title=_("Category Mismatch"),
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

	def _send_welcome_notification(self):
		"""Send a welcome message to the customer after plan activation.

		Uses Frappe's Notification Log so it appears in the customer portal
		and optionally triggers email/SMS via Notification rules.
		"""
		try:
			customer_email = frappe.db.get_value("Customer", self.customer, "email_id")
			if not customer_email:
				return

			subject = _("Welcome to {0}!").format(self.plan_title or self.warranty_plan)
			message = _(
				"Dear {customer},\n\n"
				"Your {plan} is now active.\n"
				"Coverage: {start} to {end}\n"
				"Claims allowed: {claims}\n"
				"Deductible: ₹{deductible}\n\n"
				"Thank you for choosing GoGizmo!"
			).format(
				customer=self.customer_name or self.customer,
				plan=self.plan_title or self.warranty_plan,
				start=self.start_date,
				end=self.end_date,
				claims=self.max_claims or _("Unlimited"),
				deductible=flt(self.deductible_amount),
			)

			frappe.sendmail(
				recipients=[customer_email],
				subject=subject,
				message=message,
				now=True,
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Welcome notification failed for Sold Plan {self.name}",
			)

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

	def record_claim(self, service_reference=None, claim_cost=0):
		"""Increment claims_used. Called by GoFix when a service order is created under warranty.

		Args:
			service_reference: Optional reference to the service document.
			claim_cost: Cost of this claim (for value-cap tracking).

		Raises:
			WarrantyExpiredError: If plan has expired.
			MaxClaimsReachedError: If max_claims limit reached or per-year limit exceeded.
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

		# ── Lifetime claim limit ──────────────────────────────────────────
		if self.max_claims and self.max_claims > 0:
			if (self.claims_used or 0) >= self.max_claims:
				frappe.throw(
					_("Maximum lifetime claims ({0}) reached for plan {1}").format(
						self.max_claims,
						frappe.bold(self.plan_title or self.warranty_plan),
					),
					exc=MaxClaimsReachedError,
					title=_("Claims Exhausted"),
				)

		# ── Per-year claim limit ──────────────────────────────────────────
		if self.claims_per_year and self.claims_per_year > 0 and self.start_date:
			claims_this_year = self._count_claims_current_year()
			if claims_this_year >= self.claims_per_year:
				frappe.throw(
					_("Annual claim limit ({0} per year) reached for plan {1}. "
					  "Claims reset on anniversary date.").format(
						self.claims_per_year,
						frappe.bold(self.plan_title or self.warranty_plan),
					),
					exc=MaxClaimsReachedError,
					title=_("Annual Claims Exhausted"),
				)

		# ── Value cap check ───────────────────────────────────────────────
		claim_cost = flt(claim_cost)
		if (self.max_coverage_value and flt(self.max_coverage_value) > 0
				and claim_cost > 0):
			new_total = flt(self.total_claimed_value) + claim_cost
			if new_total > flt(self.max_coverage_value):
				frappe.throw(
					_("This claim (₹{0}) would exceed the coverage cap of ₹{1}. "
					  "Already claimed: ₹{2}.").format(
						claim_cost,
						self.max_coverage_value,
						self.total_claimed_value or 0,
					),
					title=_("Coverage Cap Exceeded"),
				)

		self.claims_used = (self.claims_used or 0) + 1
		self.db_set("claims_used", self.claims_used)

		# Track cumulative claim value
		if claim_cost > 0:
			self.total_claimed_value = flt(self.total_claimed_value) + claim_cost
			self.db_set("total_claimed_value", self.total_claimed_value)

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

	def _count_claims_current_year(self):
		"""Count warranty claims in the current anniversary year.

		Anniversary year runs from start_date anniversary to next anniversary.
		Example: start_date=2025-06-15 → year 1 is 15 Jun 2025 – 14 Jun 2026.
		"""
		today = getdate(nowdate())
		start = getdate(self.start_date)

		# Calculate current anniversary window
		# Find which anniversary year we're in
		years_elapsed = today.year - start.year
		anniversary_this_year = start.replace(year=start.year + years_elapsed)
		if today < anniversary_this_year:
			years_elapsed -= 1
			anniversary_this_year = start.replace(year=start.year + years_elapsed)

		year_start = anniversary_this_year
		year_end = add_months(year_start, 12)

		count = frappe.db.count("CH Warranty Claim", {
			"sold_plan": self.name,
			"docstatus": 1,
			"claim_status": ["not in", ["Cancelled", "Rejected"]],
			"creation": ["between", [str(year_start), str(year_end)]],
		})
		return count

	def _log_vas_event(self, event_type, claim_amount=0,
	                   reference_doctype=None, reference_name=None):
		"""Write an entry to CH VAS Ledger for audit."""
		try:
			from ch_item_master.ch_item_master.doctype.ch_vas_ledger.ch_vas_ledger import log_vas_event
			log_vas_event(
				sold_plan=self.name,
				event_type=event_type,
				claim_amount=claim_amount,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
				remarks=f"{event_type} — {self.plan_title or self.warranty_plan}",
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"VAS Ledger log failed for {self.name} ({event_type})",
			)


# ── Whitelisted API ────────────────────────────────────────────────────────

@frappe.whitelist()
def get_active_plans_for_serial(serial_no, company=None) -> dict:
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
def check_warranty_status(serial_no, company=None) -> dict:
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
	priority = {
		"Own Warranty": 1, "Extended Warranty": 2,
		"Value Added Service": 3, "Protection Plan": 4,
		"Post-Repair Warranty": 5,
	}
	valid_plans.sort(key=lambda p: priority.get(p.get("plan_type"), 99))

	return {
		"warranty_covered": True,
		"warranty_status": "Under Warranty",
		"covering_plan": valid_plans[0],
		"all_plans": plans,
	}
