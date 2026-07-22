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
from frappe.utils import nowdate, now_datetime, getdate, add_months, flt

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
		if not self.sold_plan_id:
			max_id = frappe.db.sql(
				"SELECT IFNULL(MAX(sold_plan_id), 0) FROM `tabActive VAS Plans`"
			)[0][0]
			self.sold_plan_id = int(max_id) + 1

	def validate(self):
		if self.customer_phone:
			self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
		self._validate_dates()
		self._validate_plan_active()
		self._set_plan_snapshot()
		self._validate_category()
		self._validate_duplicate()
		self._auto_compute_end_date()
		self._set_sold_by()
		self._validate_source_document()

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

	def _validate_source_document(self):
		"""Every active plan must trace back to a commercial source.

		Market-standard (Croma / Reliance Digital): a care/VAS plan must originate
		from a sale (Sales Invoice / Sales Order) or be an explicit external-device
		intake. This prevents orphan plans with no commercial backing from showing
		up as Active in Customer 360. Skip the check while the row is still a Draft
		so back-office staff can stage a plan before linking the document.
		"""
		if self.docstatus == 0:
			return

		has_source = any([
			(self.get("sales_invoice") or "").strip(),
			(self.get("sales_order") or "").strip(),
			(self.get("external_device_source") or "").strip(),
		])
		if not has_source:
			frappe.throw(
				_(
					"An Active VAS Plan must be linked to a source document before submission: "
					"a Sales Invoice, a Sales Order, or an External Device Source. "
					"Set one of these fields to record where this plan was sold."
				),
				title=_("Missing Source Document"),
			)

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

	def recognize_revenue_up_to(self, as_of_date=None):
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
			frappe.log_error(
				f"Active VAS Plans {self.name}: cannot recognise revenue \u2014 "
				f"deferred account {deferred_acct!r} / revenue account {revenue_acct!r} "
				f"unresolved. Check plan overrides and Company defaults.",
				"VAS Revenue Recognition Misconfigured",
			)
			return {"delta": 0, "je": None, "recognized_total": already_recognized}

		try:
			je = frappe.new_doc("Journal Entry")
			je.posting_date = as_of
			je.company = self.company
			je.user_remark = (
				f"VAS Revenue Recognition \u2014 {self.name} \u2014 "
				f"earned {earned_to_date}/{total} through {as_of}"
			)
			je.flags.ch_system_generated_je = True
			je.append("accounts", {
				"account": deferred_acct,
				"debit_in_account_currency": delta,
			})
			je.append("accounts", {
				"account": revenue_acct,
				"credit_in_account_currency": delta,
			})
			je.flags.ignore_permissions = True
			je.insert()
			je.submit()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"VAS Revenue Recognition JE failed for {self.name}",
			)
			return {"delta": 0, "je": None, "recognized_total": already_recognized}

		new_total = already_recognized + delta
		self.db_set("revenue_recognized_amount", new_total)
		self.db_set("revenue_last_recognized_on", as_of)
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

@frappe.whitelist()
def get_active_plans_for_serial(serial_no, company=None) -> dict:
	"""Get all active active VAS plans for a serial number.

	Used by GoFix Service Request to determine warranty coverage.

	Returns:
		list[dict]: Active active VAS plans with plan details.
	"""
	filters = {
		"serial_no": serial_no,
		"status": "Active",
		"docstatus": 1,
	}
	if company:
		filters["company"] = company

	plans = frappe.get_all(
		"Active VAS Plans",
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

	plans = frappe.get_all(
		"Active VAS Plans",
		filters={
			"docstatus": 1,
			"status": ("in", ["Active", "Claimed", "Expired"]),
			"start_date": ("<=", as_of),
			"plan_price": (">", 0),
		},
		pluck="name",
	)

	summary = {"processed": 0, "posted": 0, "skipped": 0, "total_recognized": 0.0}
	for plan_name in plans:
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
			frappe.log_error(
				frappe.get_traceback(),
				f"VAS monthly revenue recognition failed for {plan_name}",
			)

	frappe.logger("vas_revenue").info(
		f"recognize_vas_revenue_monthly: {summary}"
	)
	return summary


@frappe.whitelist()
def recognize_revenue_now(sold_plan):
	"""Manual trigger for finance to recognise revenue on a single plan
	up to today (useful for period-end catch-up).
	"""
	doc = frappe.get_doc("Active VAS Plans", sold_plan)
	return doc.recognize_revenue_up_to(nowdate())
