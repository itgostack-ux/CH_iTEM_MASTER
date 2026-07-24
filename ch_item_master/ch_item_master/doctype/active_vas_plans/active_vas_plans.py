# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Active VAS Plans — tracks warranty/VAS plans issued to customers per device.

Lifecycle:
  Active → Expired (automatic, via scheduled task)
  Active → Claimed (when max_claims reached)
  Active → Void (manual, by warranty manager)
  Any → Cancelled (via amend/cancel)
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, flt, getdate, now_datetime, nowdate

from ch_item_master.config import get_int_setting, is_privileged_user, require_role_setting
from ch_item_master.id_sequences import next_numeric_id
from ch_item_master.security import ensure_company_access
from ch_item_master.ch_item_master.utils import validate_indian_phone

from ch_item_master.ch_item_master.exceptions import (
	WarrantyExpiredError,
	MaxClaimsReachedError,
	DuplicateSoldPlanError,
)


class ActiveVASPlans(Document):
	@property
	def valid_to(self):
		return self.end_date

	@valid_to.setter
	def valid_to(self, value):
		self.end_date = value

	def autoname(self):
		"""Auto-generate the active plan integration ID if not set."""
		self.sold_plan_id = next_numeric_id("active_vas_plan")

	def validate(self):
		if self._is_issuance():
			self._bind_issuance_to_source()
		if self.customer_phone:
			self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
		self._validate_dates()
		self._set_plan_snapshot()
		self._validate_category()
		self._validate_duplicate()
		self._auto_compute_end_date()
		self._set_sold_by()

	def before_update_after_submit(self):
		previous = self.get_doc_before_save()
		if not previous:
			return
		protected = (
			"company",
			"warranty_plan",
			"customer",
			"item_code",
			"serial_no",
			"is_external_device",
			"external_device_source",
			"start_date",
			"end_date",
			"sales_invoice",
			"sales_order",
			"status",
			"claims_used",
			"total_claimed_value",
			"plan_price",
			"device_purchase_price",
			"max_coverage_value",
			"custom_deferred_revenue_je",
			"revenue_recognized_amount",
			"revenue_last_recognized_on",
			"plan_snapshot_json",
			"benefit_snapshot_json",
		)
		if any(previous.get(fieldname) != self.get(fieldname) for fieldname in protected):
			frappe.throw(
				_("Submitted VAS plan lifecycle and accounting fields are system-managed."),
				frappe.PermissionError,
			)

	def _is_issuance(self):
		previous = self.get_doc_before_save()
		return self.is_new() or self.docstatus == 0 or bool(previous and previous.docstatus == 0)

	def _bind_issuance_to_source(self):
		plan = frappe.get_cached_doc("CH Warranty Plan", self.warranty_plan)
		if plan.status != "Active":
			frappe.throw(
				_("Warranty Plan {0} is {1}. Only Active plans can be issued.").format(
					frappe.bold(plan.name), plan.status
				),
				title=_("Inactive Plan"),
			)
		if plan.company and self.company != plan.company:
			frappe.throw(_("The VAS plan and issued plan must belong to the same company."))
		ensure_company_access(self.company)

		customer = frappe.db.get_value(
			"Customer",
			self.customer,
			["customer_name", "mobile_no", "ch_membership_id"],
			as_dict=True,
		)
		if not customer:
			frappe.throw(_("Customer {0} does not exist.").format(self.customer))
		item = frappe.db.get_value(
			"Item",
			self.item_code,
			["item_name", "brand", "has_serial_no"],
			as_dict=True,
		)
		if not item:
			frappe.throw(_("Item {0} does not exist.").format(self.item_code))

		source, plan_price, device_price = self._validate_commercial_source(plan, item)
		if self.docstatus == 1 and not source:
			frappe.throw(
				_("A submitted Sales Invoice or Sales Order is required before activating a VAS plan."),
				frappe.ValidationError,
			)

		if cint(self.is_external_device):
			if not cint(plan.get("allow_external_device")):
				frappe.throw(_("This warranty plan does not allow external customer devices."))
			# item_code is a generic placeholder (EXTERNAL-DEVICE or plan-specific override);
			# device identity is always in serial_no (IMEI). No strict plan.external_device_item check.
			if not (self.serial_no or "").strip() or not (self.external_device_source or "").strip():
				frappe.throw(_("External devices require an IMEI and a capture source."))
		else:
			self.external_device_source = ""
			if cint(item.has_serial_no) and not (self.serial_no or "").strip():
				frappe.throw(_("A serial number or IMEI is required for this device."))

		sale_date = None
		if source:
			sale_date = source.get("posting_date") or source.get("transaction_date")
		coverage_start = getdate(sale_date or nowdate())
		if cint(plan.get("starts_after_base_warranty")):
			base_expiry = None
			if self.serial_no:
				base_expiry = frappe.db.get_value(
					"CH Customer Device", {"serial_no": self.serial_no}, "base_warranty_expiry"
				)
			if not base_expiry:
				from ch_item_master.ch_item_master.warranty_api import get_base_warranty_expiry

				base_expiry = get_base_warranty_expiry(self.item_code, coverage_start)
			if base_expiry and getdate(base_expiry) >= coverage_start:
				coverage_start = frappe.utils.add_days(base_expiry, 1)

		self.customer_name = customer.customer_name
		self.customer_phone = customer.mobile_no
		self.membership_id = customer.ch_membership_id
		self.item_name = item.item_name
		self.brand = item.brand
		self.status = "Active"
		self.start_date = coverage_start
		self.end_date = add_months(coverage_start, cint(plan.duration_months))
		self.duration_months = cint(plan.duration_months)
		self.max_claims = cint(plan.max_claims)
		self.claims_used = 0
		self.deductible_amount = flt(plan.deductible_amount)
		self.claims_per_year = cint(plan.claims_per_year)
		self.fulfillment_type = plan.fulfillment_type or "Repair Claim"
		self.plan_price = flt(plan_price)
		self.device_purchase_price = flt(device_price)
		self.max_coverage_value = flt(device_price) if flt(device_price) > 0 else 0
		self.total_claimed_value = 0
		self.custom_deferred_revenue_je = None
		self.revenue_recognized_amount = 0
		self.revenue_last_recognized_on = None
		self.plan_title = plan.plan_name
		self.plan_type = plan.plan_type
		self.plan_snapshot_json = None
		self.benefit_snapshot_json = None
		self.sold_by = frappe.session.user

	def _validate_commercial_source(self, plan, item):
		sources = [("Sales Invoice", self.sales_invoice), ("Sales Order", self.sales_order)]
		sources = [(doctype, name) for doctype, name in sources if name]
		if not sources:
			return None, 0, 0
		if len(sources) != 1:
			frappe.throw(_("Link exactly one commercial source: Sales Invoice or Sales Order."))

		doctype, name = sources[0]
		source = frappe.get_doc(doctype, name)
		source.check_permission("read")
		if source.docstatus != 1:
			frappe.throw(_("{0} {1} must be submitted.").format(doctype, name))
		if source.company != self.company or source.customer != self.customer:
			frappe.throw(_("The commercial source company and customer must match the VAS plan."))

		service_item = plan.get("service_item")
		plan_rows = [
			row
			for row in source.get("items") or []
			if row.get("custom_warranty_plan") == plan.name
			or (service_item and row.item_code == service_item)
		]
		device_rows = [row for row in source.get("items") or [] if row.item_code == self.item_code]
		if not plan_rows and flt(plan.price) > 0:
			frappe.throw(_("The commercial source does not contain the selected warranty plan."))

		def unit_base_rate(row):
			return flt(
				row.get("base_net_rate")
				or row.get("base_rate")
				or row.get("net_rate")
				or row.get("rate")
			)

		plan_prices = {round(unit_base_rate(row), 2) for row in plan_rows}
		if len(plan_prices) > 1:
			frappe.throw(_("The commercial source has ambiguous prices for this warranty plan."))
		plan_price = next(iter(plan_prices), 0)

		if not cint(self.is_external_device):
			if not device_rows:
				frappe.throw(_("The commercial source does not contain the covered device item."))
			serial_no = (self.serial_no or "").strip()
			if serial_no:
				matching_rows = [
					row
					for row in device_rows
					if serial_no in {
						part.strip()
						for part in (row.get("serial_no") or "").replace(",", "\n").splitlines()
						if part.strip()
					}
				]
				if not matching_rows:
					frappe.throw(_("The covered serial number is not present on the commercial source."))
				device_rows = matching_rows

		device_prices = {round(unit_base_rate(row), 2) for row in device_rows}
		if len(device_prices) > 1:
			frappe.throw(_("The commercial source has ambiguous prices for the covered device."))
		device_price = next(iter(device_prices), 0)
		return source, plan_price, device_price

	def on_submit(self):
		self._sync_to_serial_lifecycle()
		self._send_welcome_notification()
		self._log_vas_event("Plan Activated")
		self._post_deferred_revenue_gl()

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")
		self._clear_serial_lifecycle()
		# ASC 606 cancellation: reverse any unamortised deferred balance back
		# to the income account so the P&L reflects the closed obligation.
		# Refund cash-flow (if any) is a separate Sales Return / Payment Entry
		# raised by ops — accounting side is authoritative for revenue.
		try:
			self._reverse_deferred_revenue_on_cancel()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"VAS deferred revenue reversal failed for {self.name}",
			)
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

	def _set_plan_snapshot(self):
		"""Capture immutable plan terms at sale time.

		The master CH Warranty Plan can change after sale. Existing customers must
		keep the commercial terms, fulfillment model, fee rules, coverage rules,
		and benefit rules that were active when their plan was issued.
		"""
		if not self.warranty_plan:
			return

		plan = frappe.get_cached_doc("CH Warranty Plan", self.warranty_plan)
		self.plan_title = self.plan_title or plan.plan_name
		self.plan_type = self.plan_type or plan.plan_type
		self.duration_months = self.duration_months or plan.duration_months
		self.max_claims = self.max_claims or plan.max_claims or 0
		self.deductible_amount = self.deductible_amount or plan.deductible_amount or 0
		self.claims_per_year = self.claims_per_year or plan.claims_per_year or 0
		self.fulfillment_type = self.fulfillment_type or plan.fulfillment_type or "Repair Claim"

		if not self.end_date and self.start_date and plan.duration_months:
			self.end_date = add_months(self.start_date, plan.duration_months)

		if self.plan_snapshot_json and self.benefit_snapshot_json:
			return

		def _rows(table_name):
			out = []
			for row in (getattr(plan, table_name, None) or []):
				out.append({
					key: value
					for key, value in row.as_dict().items()
					if key not in {
						"name", "owner", "creation", "modified", "modified_by",
						"parent", "parentfield", "parenttype", "idx", "docstatus",
					}
				})
			return out

		benefit_rules = _rows("benefit_rules")
		snapshot = {
			"snapshot_version": 1,
			"captured_at": str(now_datetime()),
			"warranty_plan": plan.name,
			"plan_name": plan.plan_name,
			"plan_type": plan.plan_type,
			"coverage_scope": plan.coverage_scope,
			"fulfillment_type": plan.fulfillment_type or "Repair Claim",
			"service_item": plan.service_item,
			"duration_months": plan.duration_months,
			"max_claims": plan.max_claims,
			"claims_per_year": plan.claims_per_year,
			"deductible_amount": plan.deductible_amount,
			"price": plan.price,
			"pricing_mode": plan.pricing_mode,
			"percentage_value": plan.percentage_value,
			"cost_to_company": plan.cost_to_company,
			"company_share_percent": plan.company_share_percent,
			"requires_approval": plan.requires_approval,
			"coverage_type_override": plan.coverage_type_override,
			"post_repair_warranty_months": plan.post_repair_warranty_months,
			"coverage_description": plan.coverage_description,
			"terms_and_conditions": plan.terms_and_conditions,
			"allow_external_device": plan.allow_external_device,
			"external_device_item": plan.external_device_item,
			"benefit_rules": benefit_rules,
			"coverage_rules": _rows("coverage_rules"),
			"fee_rules": _rows("fee_rules"),
		}

		if not self.plan_snapshot_json:
			self.plan_snapshot_json = json.dumps(snapshot, indent=2, sort_keys=True, default=str)
		if not self.benefit_snapshot_json:
			self.benefit_snapshot_json = json.dumps(benefit_rules, indent=2, sort_keys=True, default=str)

	def _validate_category(self):
		"""Ensure device category matches plan's applicable categories."""
		if not self.warranty_plan or not self.item_code:
			return
		if self.is_external_device:
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
		"""Prevent duplicate active active VAS plans for the same serial + warranty plan template.

		Multiple distinct VAS plans (different warranty_plan templates) may co-exist
		on the same serial — e.g. ADLD + Extended Warranty + Screen Protect. Only
		two active rows of the *same* warranty plan template are blocked.
		"""
		if not self.serial_no or not self.warranty_plan:
			return

		existing = frappe.db.get_value(
			"Active VAS Plans",
			{
				"serial_no": self.serial_no,
				"warranty_plan": self.warranty_plan,
				"status": "Active",
				"docstatus": 1,
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			plan_title = (
				frappe.db.get_value("CH Warranty Plan", self.warranty_plan, "plan_name")
				or self.warranty_plan
			)
			frappe.throw(
				_("Serial {0} already has an active {1} plan ({2}). "
				  "Void or cancel the existing plan first.").format(
					frappe.bold(self.serial_no),
					frappe.bold(plan_title),
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

			subject = _("Congruence Holdings | Plan Activated | {0}").format(self.name)
			plan_url = frappe.utils.get_url_to_form("Active VAS Plans", self.name)
			message = _(
				"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
				"<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>Congruence Holdings - GoGizmo Protection</div>"
				"<div style='padding:16px'>"
				"<p>Dear {customer},</p>"
				"<p>Your <b>{plan}</b> is now active.</p>"
				"<p><b>Coverage:</b> {start} to {end}<br>"
				"<b>Claims Allowed:</b> {claims}<br>"
				"<b>Deductible:</b> Rs {deductible}</p>"
				"<p><a href='{plan_url}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Plan</a></p>"
				"<p>Thank you for choosing GoGizmo.</p>"
				"</div></div>"
			).format(
				customer=frappe.utils.escape_html(self.customer_name or self.customer or "Customer"),
				plan=frappe.utils.escape_html(self.plan_title or self.warranty_plan or self.name),
				start=self.start_date,
				end=self.end_date,
				claims=self.max_claims or _("Unlimited"),
				deductible=flt(self.deductible_amount),
				plan_url=plan_url,
			)

			frappe.sendmail(
				recipients=[customer_email],
				subject=subject,
				message=message,
				# delayed=True is the default — sends via background email queue worker,
				# NOT synchronously in after_commit.  Prevents SMTP failures from
				# surfacing as HTTP 500 "Invoice creation failed" on the POS.
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Welcome notification failed for Active VAS Plans {self.name}",
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

	def _post_deferred_revenue_gl(self):
		"""Create deferred revenue journal entry when a warranty plan is activated.

		Correct accounting:
		  Debit  → Income account (reverses premature revenue recognition from the SI)
		  Credit → Deferred Revenue (parked until service obligation is delivered over time)

		The AR/Debtors account must NOT be touched — the customer already paid via the
		POS Sales Invoice; debiting Debtors again would create a phantom receivable.
		"""
		company = self.get("company") or frappe.defaults.get_user_default("Company")
		amount = flt(self.get("plan_price") or self.get("price") or self.get("amount") or 0)
		if not amount:
			return

		# Precedence for the deferred-liability account:
		#   1. Override on the linked CH Warranty Plan (per-plan finance config)
		#   2. Company.default_deferred_revenue_account
		#   3. Fuzzy account-name search on the company chart of accounts
		deferred_acct = (
			frappe.db.get_value("CH Warranty Plan", self.warranty_plan, "deferred_revenue_account")
			or frappe.db.get_value("Company", company, "default_deferred_revenue_account")
			or frappe.db.get_value("Account", {
				"account_name": ("like", "%Deferred Revenue%"), "company": company, "is_group": 0
			}, "name")
		)

		# Income account precedence:
		#   1. Override on the linked CH Warranty Plan
		#   2. Sales Invoice line income_account (existing logic below)
		#   3. Company.default_income_account
		income_acct = frappe.db.get_value(
			"CH Warranty Plan", self.warranty_plan, "revenue_account"
		) or None
		# Skip the SI-line inference when the plan already specifies its own
		# revenue_account \u2014 finance's per-plan override wins over invoice-line
		# defaults (mirrors SAP CS "Contract-level revenue posting rule").
		if not income_acct and self.sales_invoice and self.item_code:
			si_rows = frappe.get_all(
				"Sales Invoice Item",
				filters={"parent": self.sales_invoice, "item_code": self.item_code},
				fields=["idx", "income_account", "amount", "base_amount"],
				order_by="idx asc",
			)
			si_rows = [row for row in si_rows if row.get("income_account")]
			if si_rows:
				distinct_income_accounts = {row.get("income_account") for row in si_rows}
				if len(distinct_income_accounts) == 1:
					income_acct = next(iter(distinct_income_accounts))
				else:
					plan_amount = flt(amount)
					amount_matches = [
						row for row in si_rows
						if flt(row.get("amount")) == plan_amount or flt(row.get("base_amount")) == plan_amount
					]
					distinct_amount_match_accounts = {
						row.get("income_account") for row in amount_matches if row.get("income_account")
					}
					if len(distinct_amount_match_accounts) == 1:
						income_acct = next(iter(distinct_amount_match_accounts))
					else:
						# Ambiguous SI income accounts must NOT block plan activation /
						# the POS sale. Log for accounts review and fall back to the
						# company default income account below.
						frappe.log_error(
							f"Active VAS Plans {self.name}: ambiguous income account on Sales "
							f"Invoice {self.sales_invoice} for item {self.item_code} "
							f"(candidates: {sorted(distinct_income_accounts)}). "
							f"Falling back to company default income account.",
							"VAS Deferred Revenue Ambiguous Income Account",
						)
		if not income_acct:
			income_acct = frappe.db.get_value("Company", company, "default_income_account")

		if not deferred_acct or not income_acct:
			frappe.throw(
				_(
					"Deferred revenue accounting is not configured for {0}. "
					"Set Default Deferred Revenue Account and Default Income Account before selling VAS plans."
				).format(company),
				title=_("VAS Accounting Setup Required"),
			)

		try:
			je = frappe.new_doc("Journal Entry")
			# Park the deferral on the SALE date, not the coverage start:
			# the customer pays now even when coverage starts later (plans
			# that stack after the base warranty begin in a future period —
			# posting there is wrong under ASC 606 and can land in a fiscal
			# year that doesn't exist yet).
			je.posting_date = (
				frappe.db.get_value("Sales Invoice", self.sales_invoice, "posting_date")
				if self.get("sales_invoice") else None
			) or frappe.utils.today()
			je.company = company
			je.user_remark = f"Deferred Revenue — Active VAS Plans {self.name}"
			je.flags.ch_system_generated_je = True
			# Debit income (reverse premature recognition), Credit deferred liability
			je.append("accounts", {"account": income_acct, "debit_in_account_currency": amount})
			je.append("accounts", {"account": deferred_acct, "credit_in_account_currency": amount})
			je.flags.ignore_permissions = True
			je.insert()
			je.submit()
			self.db_set("custom_deferred_revenue_je", je.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Deferred revenue GL failed for Active VAS Plans {self.name}")
			raise

	# ── Public methods ───────────────────────────────────────────────────────

	def recognize_revenue_up_to(self, as_of_date=None, row_locked=False):
		"""Post straight-line revenue recognition JEs up to as_of_date.

		ASC 606 / IFRS 15 pattern used by SAP RA, Oracle Revenue Management,
		Dynamics 365 Revenue Recognition, and OnsiteGo/Assurant back-office:
		    - Plan sale posts DR Income / CR Deferred (see _post_deferred_revenue_gl)
		    - Each month between start_date and end_date, the earned portion of
		      the deferred balance is moved back to income:
		          DR Deferred Revenue
		          CR Revenue Recognition Account
		    - The scheduler ``recognize_vas_revenue_monthly`` calls this on
		      every Active plan on the 1st of each month.

		Idempotent: uses ``revenue_recognized_amount`` on the plan to compute
		the delta, so repeated calls in the same month are safe.

		Args:
		    as_of_date: Recognise revenue earned up to this date. Defaults to today.

		Returns:
		    dict: {"delta": <amount posted>, "je": <JE name or None>,
		           "recognized_total": <cumulative>}
		"""
		if self.name and not row_locked:
			locked_name = frappe.db.get_value(
				"Active VAS Plans", self.name, "name", for_update=True
			)
			if not locked_name:
				frappe.throw(
					_("Active VAS Plan {0} no longer exists.").format(self.name),
					frappe.DoesNotExistError,
				)
			self.reload()

		as_of = getdate(as_of_date or nowdate())
		start = getdate(self.start_date) if self.start_date else None
		end = getdate(self.end_date) if self.end_date else None
		total = flt(self.plan_price)

		if not start or not end or total <= 0:
			return {"delta": 0, "je": None, "recognized_total": flt(self.revenue_recognized_amount)}

		# Duration must be positive; ill-configured 0-month plans get 100% at start.
		total_days = max((end - start).days, 1)
		elapsed_days = max(0, min((as_of - start).days, total_days))
		earned_to_date = round(total * elapsed_days / total_days, 2)

		already_recognized = flt(self.revenue_recognized_amount)
		delta = round(earned_to_date - already_recognized, 2)

		if delta <= 0:
			return {"delta": 0, "je": None, "recognized_total": already_recognized}

		# Only accrue for plans still Active (not Cancelled / Void). Expired /
		# Claimed still amortise until end_date \u2014 the customer paid for the
		# term, GoGizmo earned it.
		if (self.status or "") in ("Cancelled", "Void"):
			return {"delta": 0, "je": None, "recognized_total": already_recognized}

		deferred_acct, revenue_acct = self._resolve_recognition_accounts()
		if not deferred_acct or not revenue_acct:
			frappe.throw(
				_("Revenue recognition accounts are not configured for {0}.").format(
					self.company
				),
				frappe.ValidationError,
			)

		for account in (deferred_acct, revenue_acct):
			account_company, is_group, disabled = frappe.db.get_value(
				"Account", account, ["company", "is_group", "disabled"]
			) or (None, None, None)
			if account_company != self.company or is_group or disabled:
				frappe.throw(
					_("Account {0} is not an active ledger account for {1}.").format(
						account, self.company
					),
					frappe.ValidationError,
				)

		reference_key = f"VAS Revenue Recognition | {self.name} | {as_of}"
		if frappe.db.get_value(
			"Journal Entry",
			{"company": self.company, "docstatus": 1, "user_remark": reference_key},
			"name",
		):
			frappe.throw(
				_("A revenue recognition entry already exists for {0} on {1}. Reconcile the plan before retrying.").format(
					self.name, as_of
				),
				frappe.ValidationError,
			)

		je = frappe.new_doc("Journal Entry")
		je.posting_date = as_of
		je.company = self.company
		je.user_remark = reference_key
		je.flags.ch_system_generated_je = True
		je.append("accounts", {
			"account": deferred_acct,
			"debit_in_account_currency": delta,
		})
		je.append("accounts", {
			"account": revenue_acct,
			"credit_in_account_currency": delta,
		})
		je.insert()
		je.submit()

		new_total = already_recognized + delta
		self.revenue_recognized_amount = new_total
		self.revenue_last_recognized_on = as_of
		self.db_set(
			{
				"revenue_recognized_amount": new_total,
				"revenue_last_recognized_on": as_of,
			},
			update_modified=True,
		)
		return {"delta": delta, "je": je.name, "recognized_total": new_total}

	def _reverse_deferred_revenue_on_cancel(self):
		"""On plan cancellation, flush any unamortised deferred balance back to income.

		Follows SAP CS "Contract Cancellation \u2013 No Refund" pattern:
		    DR Deferred Revenue (unamortised portion)
		    CR Revenue Recognition Account

		The customer's cash refund (if any) is booked separately as a Sales
		Return / Payment Entry by ops \u2014 this method only closes the P&L side
		of the service obligation. If the plan was already 100% amortised
		(cancelled after end_date) this is a no-op.
		"""
		total = flt(self.plan_price)
		if total <= 0:
			return

		# Cancellation-time recognition catches up ledger through today.
		already_recognized = flt(self.revenue_recognized_amount)
		unamortised = round(total - already_recognized, 2)
		if unamortised <= 0:
			return

		deferred_acct, revenue_acct = self._resolve_recognition_accounts()
		if not deferred_acct or not revenue_acct:
			frappe.log_error(
				f"Active VAS Plans {self.name}: cancellation reversal skipped \u2014 "
				f"account resolution failed.",
				"VAS Cancellation Reversal Misconfigured",
			)
			return

		je = frappe.new_doc("Journal Entry")
		je.posting_date = nowdate()
		je.company = self.company
		je.user_remark = (
			f"VAS Plan Cancellation \u2014 {self.name} \u2014 "
			f"flushing unamortised deferred balance {unamortised} to income"
		)
		je.flags.ch_system_generated_je = True
		je.append("accounts", {
			"account": deferred_acct,
			"debit_in_account_currency": unamortised,
		})
		je.append("accounts", {
			"account": revenue_acct,
			"credit_in_account_currency": unamortised,
		})
		je.flags.ignore_permissions = True
		je.insert()
		je.submit()
		self.db_set("revenue_recognized_amount", already_recognized + unamortised)

	def _resolve_recognition_accounts(self):
		"""Return (deferred_revenue_account, revenue_account) using the SAP-style
		precedence: per-plan override \u2192 Sales Invoice line income_account \u2192
		Company defaults. Falls through to (None, None) when finance hasn't set
		anything up so the caller can log + skip cleanly.
		"""
		company = self.get("company") or frappe.defaults.get_user_default("Company")

		deferred_acct = (
			frappe.db.get_value("CH Warranty Plan", self.warranty_plan, "deferred_revenue_account")
			or frappe.db.get_value("Company", company, "default_deferred_revenue_account")
		)

		revenue_acct = frappe.db.get_value(
			"CH Warranty Plan", self.warranty_plan, "revenue_account"
		)
		if not revenue_acct and self.sales_invoice and self.item_code:
			revenue_acct = frappe.db.get_value(
				"Sales Invoice Item",
				{"parent": self.sales_invoice, "item_code": self.item_code},
				"income_account",
			)
		if not revenue_acct:
			revenue_acct = frappe.db.get_value(
				"Company", company, "default_income_account"
			)

		return deferred_acct, revenue_acct

	def record_claim(self, service_reference=None, claim_cost=0,
	                 reference_doctype=None, reference_name=None):
		"""Increment claims_used. Called by GoFix when a service order is created under warranty.

		Args:
			service_reference: Optional reference to the service document.
			claim_cost: Cost of this claim (for value-cap tracking).
			reference_doctype / reference_name: The source document consuming this
				claim (CH Warranty Claim / Service Request). Used as the idempotency
				key — if a "Claim Used" ledger row already exists for the same
				reference the counter is NOT bumped again. This guards against
				double-counting when a CH Warranty Claim spawns a Service Request
				and both call record_claim.

		Raises:
			WarrantyExpiredError: If plan has expired.
			MaxClaimsReachedError: If max_claims limit reached or per-year limit exceeded.
		"""
		if not self.name or not frappe.db.get_value(
			"Active VAS Plans", self.name, "name", for_update=True
		):
			frappe.throw(_("Active VAS Plan does not exist."), frappe.DoesNotExistError)
		self.reload()
		today = getdate(nowdate())

		# ── Idempotency guard ────────────────────────────────────────────
		# Market-standard consumption ledger dedup (SAP CS "Warranty Claim
		# Reference", Oracle Service "Coverage Consumption" record).
		ref_dt = reference_doctype or ("Service Request" if service_reference else None)
		ref_nm = reference_name or service_reference
		if ref_dt and ref_nm:
			already = frappe.db.exists(
				"CH VAS Ledger",
				{
					"sold_plan": self.name,
					"event_type": "Claim Used",
					"reference_doctype": ref_dt,
					"reference_name": ref_nm,
				},
			)
			if already:
				return  # Already counted — nothing to do.

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

		# Ledger row is our audit + idempotency record. Written here (not by the
		# caller) so record_claim always leaves the ledger in a consistent state.
		self._log_vas_event(
			"Claim Used",
			claim_amount=flt(claim_cost),
			reference_doctype=ref_dt,
			reference_name=ref_nm,
		)

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

_VAS_VIEW_ROLES = (
	"CH Warranty Manager",
	"Service Manager",
	"Sales Manager",
	"Sales User",
)
_VAS_FINANCE_ROLES = ("Accounts Manager", "CH Warranty Manager")


def _require_named_permission(doctype, name, permission_type="read"):
	if not frappe.has_permission(
		doctype,
		ptype=permission_type,
		doc=name,
		user=frappe.session.user,
		throw=False,
	):
		frappe.throw(
			_("You do not have {0} permission for {1} {2}.").format(
				permission_type, doctype, name
			),
			frappe.PermissionError,
		)


def _resolve_serial_location(serial_no, plan=None, require_named_reads=False):
	serial_no = (serial_no or "").strip()
	lifecycle_name = None
	if serial_no:
		for filters in (
			{"serial_no": serial_no},
			{"imei_number": serial_no},
			{"imei_number_2": serial_no},
		):
			lifecycle_name = frappe.db.get_value("CH Serial Lifecycle", filters, "name")
			if lifecycle_name:
				break

	lifecycle = None
	if lifecycle_name:
		if require_named_reads:
			_require_named_permission("CH Serial Lifecycle", lifecycle_name)
		lifecycle = frappe.db.get_value(
			"CH Serial Lifecycle",
			lifecycle_name,
			["serial_no", "current_company", "current_warehouse", "current_store"],
			as_dict=True,
		)

	serial_name = None
	if serial_no and frappe.db.exists("Serial No", serial_no):
		serial_name = serial_no
	elif lifecycle and lifecycle.get("serial_no") and frappe.db.exists(
		"Serial No", lifecycle.get("serial_no")
	):
		serial_name = lifecycle.get("serial_no")

	serial_warehouse = None
	if serial_name:
		if require_named_reads:
			_require_named_permission("Serial No", serial_name)
		serial_warehouse = frappe.db.get_value("Serial No", serial_name, "warehouse")

	company = lifecycle.get("current_company") if lifecycle else None
	warehouse = lifecycle.get("current_warehouse") if lifecycle else None
	store = lifecycle.get("current_store") if lifecycle else None
	if warehouse and serial_warehouse and warehouse != serial_warehouse:
		frappe.throw(
			_("Serial {0} has conflicting warehouse references.").format(serial_no),
			frappe.ValidationError,
		)
	warehouse = warehouse or serial_warehouse

	if plan and plan.get("sales_invoice"):
		invoice_name = plan.get("sales_invoice")
		if require_named_reads:
			_require_named_permission("Sales Invoice", invoice_name)
		invoice = frappe.db.get_value(
			"Sales Invoice",
			invoice_name,
			["company", "customer", "docstatus", "set_warehouse", "pos_profile"],
			as_dict=True,
		)
		if not invoice or invoice.docstatus != 1:
			frappe.throw(
				_("Sales Invoice {0} is not submitted.").format(invoice_name),
				frappe.ValidationError,
			)
		if invoice.company != plan.get("company") or (
			plan.get("customer") and invoice.customer != plan.get("customer")
		):
			frappe.throw(
				_("Sales Invoice {0} does not belong to this VAS plan.").format(
					invoice_name
				),
				frappe.ValidationError,
			)
		company = company or invoice.company
		invoice_warehouse = invoice.set_warehouse
		if not invoice_warehouse and invoice.pos_profile:
			invoice_warehouse = frappe.db.get_value(
				"POS Profile", invoice.pos_profile, "warehouse"
			)
		if not invoice_warehouse:
			invoice_rows = frappe.get_all(
				"Sales Invoice Item",
				filters={"parent": invoice_name, "item_code": plan.get("item_code")},
				fields=["warehouse", "serial_no"],
				limit_page_length=100,
			)
			matching_rows = [
				row for row in invoice_rows
				if not serial_no
				or serial_no in {
					value.strip()
					for value in (row.get("serial_no") or "").replace(",", "\n").splitlines()
					if value.strip()
				}
			]
			warehouses = {row.get("warehouse") for row in matching_rows if row.get("warehouse")}
			if len(warehouses) == 1:
				invoice_warehouse = next(iter(warehouses))
		if warehouse and invoice_warehouse and warehouse != invoice_warehouse:
			frappe.throw(
				_("The VAS plan and its Sales Invoice reference different warehouses."),
				frappe.ValidationError,
			)
		warehouse = warehouse or invoice_warehouse

	if warehouse:
		if require_named_reads:
			_require_named_permission("Warehouse", warehouse)
		warehouse_company = frappe.db.get_value(
			"Warehouse", warehouse, ["company", "disabled"], as_dict=True
		)
		if not warehouse_company or warehouse_company.disabled:
			frappe.throw(_("Warehouse {0} is not active.").format(warehouse), frappe.ValidationError)
		if company and warehouse_company.company != company:
			frappe.throw(
				_("Warehouse {0} belongs to a different company.").format(warehouse),
				frappe.ValidationError,
			)
		company = company or warehouse_company.company

	store_rows = []
	if store:
		store_rows = frappe.get_all(
			"CH Store",
			filters={"name": store, "disabled": 0},
			fields=["name", "company", "warehouse"],
			limit_page_length=2,
		)
	elif warehouse:
		store_rows = frappe.get_all(
			"CH Store",
			filters={"warehouse": warehouse, "disabled": 0},
			fields=["name", "company", "warehouse"],
			limit_page_length=2,
		)
		if len(store_rows) > 1:
			frappe.throw(
				_("Warehouse {0} maps to more than one active store.").format(warehouse),
				frappe.ValidationError,
			)
	if store and not store_rows:
		frappe.throw(
			_("Store {0} is not active or does not exist.").format(store),
			frappe.ValidationError,
		)
	if store_rows:
		store_row = store_rows[0]
		store = store_row.name
		if require_named_reads:
			_require_named_permission("CH Store", store)
		if (company and store_row.company != company) or (
			warehouse and store_row.warehouse != warehouse
		):
			frappe.throw(
				_("Store {0} is inconsistent with the serial location.").format(store),
				frappe.ValidationError,
			)
		company = company or store_row.company
		warehouse = warehouse or store_row.warehouse

	if plan and company and plan.get("company") != company:
		frappe.throw(
			_("The VAS plan company does not match the serial location."),
			frappe.ValidationError,
		)

	return {
		"serial_name": serial_name,
		"lifecycle_name": lifecycle_name,
		"company": company,
		"warehouse": warehouse,
		"store": store,
	}


def _assert_exact_serial_scope(anchor, action):
	company = anchor.get("company")
	warehouse = anchor.get("warehouse")
	store = anchor.get("store")
	if not company:
		frappe.throw(
			_("The serial company cannot be resolved for {0}.").format(action),
			frappe.PermissionError,
		)
	ensure_company_access(company)
	if is_privileged_user():
		return
	if not warehouse:
		frappe.throw(
			_("The serial warehouse cannot be resolved for {0}.").format(action),
			frappe.PermissionError,
		)
	try:
		from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
	except (ImportError, ModuleNotFoundError):
		frappe.throw(_("Store scope validation is unavailable."), frappe.PermissionError)
	assert_user_has_store_scope(
		store=store,
		warehouse=warehouse,
		company=company,
		msg=_("You are not permitted to access this serial's location."),
	)


def _load_authorized_active_plans(serial_no, company=None):
	serial_no = (serial_no or "").strip()
	company = (company or "").strip() or None
	if not serial_no or len(serial_no) > 140:
		frappe.throw(_("Provide a valid serial number."), frappe.ValidationError)
	require_role_setting(
		"warranty_dashboard_roles",
		_VAS_VIEW_ROLES,
		action=_("view warranty coverage"),
	)
	frappe.has_permission("Active VAS Plans", "read", throw=True)

	limit = min(get_int_setting("warranty_dashboard_device_limit", 100, minimum=1), 500)
	filters = {"serial_no": serial_no, "status": "Active", "docstatus": 1}
	if company:
		filters["company"] = company
	plan_names = frappe.get_all(
		"Active VAS Plans",
		filters=filters,
		pluck="name",
		order_by="end_date desc",
		limit_page_length=limit,
	)
	plans = []
	for name in plan_names:
		_require_named_permission("Active VAS Plans", name)
		plans.append(frappe.get_doc("Active VAS Plans", name))

	companies = {doc.company for doc in plans if doc.company}
	if company and companies and companies != {company}:
		frappe.throw(_("The requested company does not match the VAS plan."), frappe.PermissionError)
	if not company and len(companies) > 1:
		frappe.throw(
			_("This serial has active plans in more than one company. Select a company."),
			frappe.ValidationError,
		)

	anchor_plan = plans[0] if plans else None
	anchor = _resolve_serial_location(
		serial_no,
		plan=anchor_plan,
		require_named_reads=True,
	)
	if company and anchor.get("company") and anchor["company"] != company:
		frappe.throw(_("The requested company does not match the serial location."), frappe.PermissionError)
	if plans or anchor.get("serial_name") or anchor.get("lifecycle_name"):
		_assert_exact_serial_scope(anchor, _("view warranty coverage"))
	return plans

@frappe.whitelist()
def get_active_plans_for_serial(serial_no, company=None) -> dict:
	"""Get all active active VAS plans for a serial number.

	Used by GoFix Service Request to determine warranty coverage.

	Returns:
		list[dict]: Active active VAS plans with plan details.
	"""
	plan_docs = _load_authorized_active_plans(serial_no, company)
	fields = (
		"name", "warranty_plan", "plan_title", "plan_type",
		"start_date", "end_date", "claims_used", "max_claims",
		"deductible_amount", "customer", "customer_name",
		"item_code", "item_name", "brand",
	)
	plans = [frappe._dict({field: doc.get(field) for field in fields}) for doc in plan_docs]

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


# ── Scheduled jobs ─────────────────────────────────────────────────────────

def recognize_vas_revenue_monthly(as_of_date=None):
	"""Scheduled monthly job — post straight-line revenue recognition JEs.

	Runs on the 1st of every month (see ch_erp15 hooks). For each currently
	Active plan, moves the earned portion of the deferred balance from the
	Deferred Revenue liability account into the Revenue Recognition income
	account. Idempotent — safe to re-run any time.

	This is the ASC 606 / IFRS 15 counterpart to
	:meth:`ActiveVASPlans._post_deferred_revenue_gl` which parks the full
	plan price on activation. Together they implement the same revenue
	profile SAP RA, Oracle Revenue Management and Dynamics 365 Revenue
	Recognition use for service contracts and extended warranties.
	"""
	as_of = getdate(as_of_date or nowdate())
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 5000)
	cursor_key = f"ch_item_master:vas_revenue_cursor:{as_of}"
	cursor = frappe.cache.get_value(cursor_key) or ""

	def due_plans(after_name=""):
		return frappe.db.sql(
			"""
				SELECT name
				FROM `tabActive VAS Plans`
				WHERE docstatus = 1
				  AND status IN ('Active', 'Claimed', 'Expired')
				  AND start_date < %(as_of)s
				  AND plan_price > 0
				  AND IFNULL(revenue_recognized_amount, 0) < plan_price
				  AND (
					revenue_last_recognized_on IS NULL
					OR revenue_last_recognized_on < %(as_of)s
				  )
				  AND name > %(after_name)s
				ORDER BY name ASC
				LIMIT %(fetch_limit)s
			""",
			{
				"as_of": as_of,
				"after_name": after_name,
				"fetch_limit": batch_limit + 1,
			},
			pluck=True,
		)

	plans = due_plans(cursor)
	if not plans and cursor:
		plans = due_plans()
	if not plans:
		frappe.cache.delete_value(cursor_key)
		return {
			"processed": 0,
			"posted": 0,
			"skipped": 0,
			"failed": 0,
			"total_recognized": 0.0,
			"has_more": False,
		}

	work = plans[:batch_limit]
	summary = {
		"processed": 0,
		"posted": 0,
		"skipped": 0,
		"failed": 0,
		"total_recognized": 0.0,
		"has_more": len(plans) > batch_limit,
	}
	for index, plan_name in enumerate(work):
		save_point = f"vas_revenue_plan_{index}"
		frappe.db.savepoint(save_point=save_point)
		try:
			plan = frappe.get_doc("Active VAS Plans", plan_name)
			result = plan.recognize_revenue_up_to(as_of)
			summary["processed"] += 1
			if result.get("delta", 0) > 0:
				summary["posted"] += 1
				summary["total_recognized"] += flt(result["delta"])
			else:
				summary["skipped"] += 1
		except Exception:
			frappe.db.rollback(save_point=save_point)
			summary["failed"] += 1
			frappe.log_error(
				frappe.get_traceback(),
				f"VAS monthly revenue recognition failed for {plan_name}",
			)

	if summary["has_more"]:
		frappe.cache.set_value(cursor_key, work[-1])
	else:
		frappe.cache.delete_value(cursor_key)
	frappe.logger("vas_revenue").info(
		f"recognize_vas_revenue_monthly: {summary}"
	)
	return summary


@frappe.whitelist(methods=["POST"])
def recognize_revenue_now(sold_plan):
	"""Manual trigger for finance to recognise revenue on a single plan
	up to today (useful for period-end catch-up).
	"""
	sold_plan = (sold_plan or "").strip()
	if not sold_plan or len(sold_plan) > 140:
		frappe.throw(_("Provide a valid Active VAS Plan."), frappe.ValidationError)
	require_role_setting(
		"vas_finance_roles",
		_VAS_FINANCE_ROLES,
		action=_("recognize VAS revenue"),
	)
	locked_name = frappe.db.get_value(
		"Active VAS Plans", sold_plan, "name", for_update=True
	)
	if not locked_name:
		frappe.throw(
			_("Active VAS Plan {0} does not exist.").format(sold_plan),
			frappe.DoesNotExistError,
		)
	doc = frappe.get_doc("Active VAS Plans", sold_plan)
	_require_named_permission("Active VAS Plans", doc.name, "write")
	frappe.has_permission("Journal Entry", "create", throw=True)
	frappe.has_permission("Journal Entry", "submit", throw=True)
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted VAS plans can recognize revenue."), frappe.ValidationError)
	if doc.status in ("Cancelled", "Void"):
		frappe.throw(_("Revenue cannot be recognized for a closed VAS plan."), frappe.ValidationError)
	anchor = _resolve_serial_location(doc.serial_no, plan=doc)
	_assert_exact_serial_scope(anchor, _("recognize VAS revenue"))
	return doc.recognize_revenue_up_to(nowdate(), row_locked=True)
