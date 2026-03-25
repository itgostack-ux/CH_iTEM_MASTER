# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Warranty Claim — warranty/service claim lifecycle.

Workflow:
  Draft → (submit) → Pending Approval (if GoGizmo pays) or Approved (if no approval needed)
  Pending Approval → Approved / Rejected
  Approved → Ticket Created (GoFix Service Request auto-created)
  Ticket Created → In Repair (synced from GoFix)
  In Repair → Repair Complete
  Repair Complete → Closed (after settlement)
  Any → Cancelled (via cancel)
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, now_datetime, getdate, flt

from buyback.utils import validate_indian_phone


# GoGizmo retail company (warranty issuer)
GOGIZMO_COMPANY = "GoGizmo Retail Pvt Ltd"
# GoFix service company (repair provider)
GOFIX_COMPANY = "GoFix Services Pvt Ltd"


class CHWarrantyClaim(Document):
	def autoname(self):
		if not self.claim_id:
			max_id = frappe.db.sql(
				"SELECT IFNULL(MAX(claim_id), 0) FROM `tabCH Warranty Claim`"
			)[0][0]
			self.claim_id = int(max_id) + 1

	def validate(self):
		if self.customer_phone:
			self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
		if self.manufacturer_contact_phone:
			self.manufacturer_contact_phone = validate_indian_phone(
				self.manufacturer_contact_phone, "Manufacturer Contact Phone"
			)
		self._set_title()
		self._set_reported_by()
		if not self.claim_status:
			self.claim_status = "Draft"

	def before_submit(self):
		self._lookup_warranty_coverage()
		self._determine_coverage_type()
		self._calculate_cost_split()
		self._check_approval_needed()

		if self.requires_approval:
			self.claim_status = "Pending Approval"
			self.approval_status = "Pending"
		else:
			self.claim_status = "Approved"
			self.approval_status = ""

		self._log("Submitted", "Draft", self.claim_status,
		          "Claim submitted. " + (
		              "Pending GoGizmo Head approval." if self.requires_approval
		              else "Auto-approved (no GoGizmo share)."
		          ), save=False)

	def on_submit(self):
		if self.coverage_type == "Manufacturer Warranty":
			# Device goes to manufacturer, not GoFix
			self._send_to_manufacturer(from_submit=True)
		elif self.claim_status == "Approved":
			# Create GoFix ticket immediately
			self._create_gofix_ticket(from_submit=True)

	def on_cancel(self):
		old = self.claim_status
		self.claim_status = "Cancelled"
		self.db_set("claim_status", "Cancelled")
		self._log("Cancelled", old, "Cancelled", "Claim cancelled.", save=False)

	# ── Public Actions ───────────────────────────────────────────────────

	@frappe.whitelist()
	def approve(self, remarks=None, approved_amount=None):
		"""GoGizmo Head approves the claim → creates GoFix ticket.

		Supports partial approval: if approved_amount < gogizmo_share,
		the difference is shifted to customer_share.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted before approval."))
		if self.claim_status != "Pending Approval":
			frappe.throw(_("Claim is not pending approval (current: {0}).").format(
				self.claim_status))

		old = self.claim_status
		self.approval_status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_at = now_datetime()
		self.claim_status = "Approved"

		# Partial approval: recalculate shares
		if approved_amount is not None:
			approved_amt = flt(approved_amount)
			if approved_amt < 0:
				frappe.throw(_("Approved amount cannot be negative."))
			if approved_amt > flt(self.gogizmo_share):
				approved_amt = flt(self.gogizmo_share)
			self.approved_amount = approved_amt
			# Shift the difference to customer
			reduction = flt(self.gogizmo_share) - approved_amt
			self.gogizmo_share = approved_amt
			self.customer_share = flt(self.customer_share) + reduction
		else:
			self.approved_amount = flt(self.gogizmo_share)

		self.db_set({
			"approval_status": "Approved",
			"approved_by": self.approved_by,
			"approved_at": self.approved_at,
			"approved_amount": self.approved_amount,
			"gogizmo_share": self.gogizmo_share,
			"customer_share": self.customer_share,
			"claim_status": "Approved",
		})

		partial_note = ""
		if approved_amount is not None and flt(approved_amount) < flt(self.estimated_repair_cost):
			partial_note = f" (partial: ₹{flt(approved_amount)} of ₹{flt(self.estimated_repair_cost)})"

		self._log("Approved", old, "Approved",
		          (remarks or f"Approved by {frappe.session.user}") + partial_note)

		# Now create GoFix ticket
		self._create_gofix_ticket()

	@frappe.whitelist()
	def reject(self, reason=None):
		"""GoGizmo Head rejects the claim."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted before rejection."))
		if self.claim_status != "Pending Approval":
			frappe.throw(_("Claim is not pending approval (current: {0}).").format(
				self.claim_status))

		old = self.claim_status
		self.approval_status = "Rejected"
		self.approved_by = frappe.session.user
		self.approved_at = now_datetime()
		self.rejection_reason = reason or ""
		self.claim_status = "Rejected"

		self.db_set({
			"approval_status": "Rejected",
			"approved_by": self.approved_by,
			"approved_at": self.approved_at,
			"rejection_reason": self.rejection_reason,
			"claim_status": "Rejected",
		})

		self._log("Rejected", old, "Rejected",
		          reason or f"Rejected by {frappe.session.user}")

	@frappe.whitelist()
	def mark_repair_complete(self, remarks=None):
		"""Called when GoFix completes the repair, or device returned from manufacturer."""
		if self.claim_status not in ("Ticket Created", "In Repair", "Sent to Manufacturer"):
			frappe.throw(_("Cannot mark complete — current status: {0}").format(
				self.claim_status))

		old = self.claim_status
		self.repair_status = "Completed"
		self.repair_completion_date = nowdate()
		self.claim_status = "Repair Complete"

		# If returning from manufacturer, set actual return date
		if old == "Sent to Manufacturer" and not self.actual_return_date:
			self.actual_return_date = nowdate()

		update_fields = {
			"repair_status": "Completed",
			"repair_completion_date": self.repair_completion_date,
			"claim_status": "Repair Complete",
		}
		if self.actual_return_date:
			update_fields["actual_return_date"] = self.actual_return_date

		self.db_set(update_fields)

		# Record warranty claim usage on the sold plan
		if self.sold_plan:
			try:
				from ch_item_master.ch_item_master.warranty_api import record_warranty_claim
				record_warranty_claim(
					serial_no=self.serial_no,
					service_reference=self.name,
					company=self.company,
				)
			except Exception as e:
				frappe.log_error(
					f"Failed to record warranty claim for {self.name}: {e}",
					"Warranty Claim - Record Claim Error",
				)

		self._log("Repair Complete", old, "Repair Complete",
		          remarks or ("Device returned from manufacturer" if old == "Sent to Manufacturer"
		                      else "Repair completed by GoFix"))

	@frappe.whitelist()
	def close_claim(self, remarks=None):
		"""Close the claim after settlement."""
		if self.claim_status not in ("Repair Complete", "Approved", "Rejected", "Sent to Manufacturer"):
			frappe.throw(_("Cannot close — current status: {0}").format(
				self.claim_status))

		old = self.claim_status

		# ── Calculate total claim cost ────────────────────────────────────
		self.total_claim_cost = (
			flt(self.estimated_repair_cost)
			+ flt(self.logistics_cost)
			+ flt(self.third_party_repair_cost)
		)

		self.claim_status = "Closed"
		self.settlement_status = "Settled"

		self.db_set({
			"claim_status": "Closed",
			"settlement_status": "Settled",
			"total_claim_cost": self.total_claim_cost,
		})

		self._log("Closed", old, "Closed", remarks or "Claim closed")

		# Log to VAS ledger
		if self.sold_plan:
			try:
				from ch_item_master.ch_item_master.doctype.ch_vas_ledger.ch_vas_ledger import log_vas_event
				log_vas_event(
					sold_plan=self.sold_plan,
					event_type="Claim Used",
					claim_amount=flt(self.total_claim_cost),
					reference_doctype="CH Warranty Claim",
					reference_name=self.name,
					remarks=f"Claim closed — {self.issue_description or ''}",
				)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"VAS Ledger log failed for claim {self.name}",
				)

	# ── Private Methods ──────────────────────────────────────────────────

	def _set_title(self):
		self.title = f"{self.serial_no} — {self.customer_name or self.customer}"

	def _set_reported_by(self):
		if not self.reported_by:
			self.reported_by = frappe.session.user

	def _lookup_warranty_coverage(self):
		"""Look up warranty from CH Sold Plan + CH Serial Lifecycle.

		If sold_plan is pre-set (e.g. user selected a specific plan from POS),
		use that plan directly instead of auto-detecting.
		"""
		from ch_item_master.ch_item_master.doctype.ch_sold_plan.ch_sold_plan import (
			check_warranty_status,
		)

		# If a specific sold plan was selected, use it directly
		if self.sold_plan:
			try:
				sp = frappe.get_doc("CH Sold Plan", self.sold_plan)
				if sp.status == "Active" and sp.docstatus == 1:
					self.warranty_status = "Under Warranty"
					self.warranty_plan = sp.warranty_plan
					self.plan_type = sp.plan_type
					self.warranty_start_date = sp.start_date
					self.warranty_end_date = sp.end_date
					self.claims_used = sp.claims_used or 0
					self.max_claims = sp.max_claims or 0
					self.deductible_amount = flt(sp.deductible_amount)
					return
			except frappe.DoesNotExistError:
				pass  # Fall through to auto-detection

		result = check_warranty_status(self.serial_no, self.company)

		self.warranty_status = result.get("warranty_status", "No Warranty")

		if result.get("warranty_covered") and result.get("covering_plan"):
			plan = result["covering_plan"]
			self.sold_plan = plan.get("name")
			self.warranty_plan = plan.get("warranty_plan")
			self.plan_type = plan.get("plan_type")
			self.warranty_start_date = plan.get("start_date")
			self.warranty_end_date = plan.get("end_date")
			self.claims_used = plan.get("claims_used", 0)
			self.max_claims = plan.get("max_claims", 0)
			self.deductible_amount = flt(plan.get("deductible_amount", 0))

		# If no sold plan covers device, check manufacturer warranty from Serial No
		if not result.get("warranty_covered"):
			sn_name = self.serial_no
			if frappe.db.exists("Serial No", sn_name):
				mfr_expiry = frappe.db.get_value("Serial No", sn_name, "warranty_expiry_date")
				if mfr_expiry and getdate(mfr_expiry) >= getdate(nowdate()):
					self.warranty_status = "Under Warranty"
					self.warranty_end_date = mfr_expiry
					# Mark as manufacturer warranty (no sold plan, brand/OEM covers it)
					self.flags.is_manufacturer_warranty = True

		# Also try enriching from lifecycle
		lc_name = self.serial_no
		if not frappe.db.exists("CH Serial Lifecycle", lc_name):
			lc_name = frappe.db.get_value(
				"CH Serial Lifecycle", {"imei_number": self.serial_no}, "name"
			) or frappe.db.get_value(
				"CH Serial Lifecycle", {"imei_number_2": self.serial_no}, "name"
			)

		if lc_name and frappe.db.exists("CH Serial Lifecycle", lc_name):
			lc = frappe.db.get_value(
				"CH Serial Lifecycle", lc_name,
				["item_code", "item_name", "imei_number", "customer", "customer_name"],
				as_dict=True,
			)
			if lc:
				if not self.item_code and lc.item_code:
					self.item_code = lc.item_code
				if not self.imei_number and lc.imei_number:
					self.imei_number = lc.imei_number
				if not self.customer and lc.customer:
					self.customer = lc.customer

	def _determine_coverage_type(self):
		"""Set coverage_type based on warranty lookup."""
		if getattr(self.flags, "is_manufacturer_warranty", False):
			# Device under OEM/brand warranty — GoGizmo facilitates, manufacturer repairs
			self.coverage_type = "Manufacturer Warranty"
		elif self.warranty_status == "Under Warranty":
			self.coverage_type = "In Warranty"
		elif self.warranty_status == "Extended":
			self.coverage_type = "In Warranty"
		elif self.warranty_status in ("Out of Warranty", "Expired"):
			self.coverage_type = "Out of Warranty"
		else:
			self.coverage_type = "Out of Warranty"

		# If deductible > 0 and in warranty, it's partial
		if self.coverage_type == "In Warranty" and flt(self.deductible_amount) > 0:
			self.coverage_type = "Partial Coverage"

	def _calculate_cost_split(self):
		"""Calculate how much each party pays."""
		est = flt(self.estimated_repair_cost)

		if self.coverage_type == "Manufacturer Warranty":
			# Manufacturer covers repair — no cost to GoGizmo or customer
			self.gogizmo_share = 0
			self.gofix_share = 0
			self.customer_share = 0
		elif self.coverage_type == "In Warranty":
			# GoGizmo pays everything
			self.gogizmo_share = est
			self.gofix_share = 0
			self.customer_share = 0
		elif self.coverage_type == "Partial Coverage":
			# Customer pays deductible, GoGizmo pays the rest
			self.customer_share = flt(self.deductible_amount)
			self.gogizmo_share = max(0, est - self.customer_share)
			self.gofix_share = 0
		else:
			# Out of Warranty — customer pays GoFix directly
			self.gogizmo_share = 0
			self.gofix_share = 0
			self.customer_share = est

	def _check_approval_needed(self):
		"""GoGizmo Head must approve if GoGizmo is paying any amount.

		Post-Repair Warranty: GoFix handles directly, skip approval.
		Manufacturer Warranty: Manufacturer covers, no GoGizmo approval needed.
		Also auto-flags swaps outside warranty window (#11) and warranty giveaways (#9).
		"""
		if self.plan_type == "Post-Repair Warranty":
			self.requires_approval = 0
		elif self.coverage_type == "Manufacturer Warranty":
			self.requires_approval = 0
		else:
			self.requires_approval = 1 if flt(self.gogizmo_share) > 0 else 0

		# #11: Auto-flag swap outside warranty window
		if (self.warranty_end_date
				and getdate(self.claim_date or nowdate()) > getdate(self.warranty_end_date)):
			self.requires_approval = 1
			self._create_exception_request(
				"Swap Outside Window",
				f"Claim raised on {self.claim_date or nowdate()} "
				f"but warranty expired on {self.warranty_end_date}",
			)

		# #9: Warranty giveaway — when GoGizmo bears cost
		if flt(self.gogizmo_share) > 0:
			self._create_exception_request(
				"Warranty Giveaway",
				f"GoGizmo bearing ₹{self.gogizmo_share} for {self.coverage_type}",
				requested_value=flt(self.gogizmo_share),
				original_value=flt(self.estimated_repair_cost),
			)

	def _create_exception_request(self, exception_type, reason,
	                               requested_value=0, original_value=0):
		"""Helper: create a CH Exception Request linked to this warranty claim."""
		if not frappe.db.exists("CH Exception Type", exception_type):
			return
		try:
			from ch_item_master.ch_item_master.exception_api import raise_exception
			raise_exception(
				exception_type=exception_type,
				company=self.company,
				reason=reason,
				requested_value=flt(requested_value),
				original_value=flt(original_value),
				reference_doctype="CH Warranty Claim",
				reference_name=self.name,
				item_code=self.item_code,
				serial_no=self.serial_no,
				customer=self.customer,
			)
		except Exception:
			frappe.log_error(f"Exception request creation failed for {self.name}")

	def _create_gofix_ticket(self, from_submit=False):
		"""Create a GoFix Service Request from this claim."""
		if self.service_request:
			return  # Already created

		# ── Processing fee gate ───────────────────────────────────────────
		# If a processing fee is required, it must be paid/waived before repair starts.
		if (self.processing_fee_status == "Pending"
				and flt(self.processing_fee_amount) > 0):
			frappe.throw(
				_("Processing fee of ₹{0} must be paid or waived before creating "
				  "a repair ticket. Update Processing Fee Status first.").format(
					self.processing_fee_amount),
				title=_("Processing Fee Pending"),
			)

		try:
			# Get GoFix warehouse
			gofix_warehouse = frappe.db.get_value(
				"Warehouse", {"company": GOFIX_COMPANY, "is_group": 0},
				"name", order_by="creation asc"
			) or ""

			sr = frappe.new_doc("Service Request")
			sr.customer = self.customer
			sr.customer_name = self.customer_name
			sr.contact_number = self.customer_phone or ""
			sr.company = GOFIX_COMPANY
			sr.device_item = self.item_code
			sr.serial_no = self.serial_no
			sr.brand = self.brand
			sr.source_warehouse = gofix_warehouse
			sr.service_date = nowdate()

			# Warranty info
			sr.warranty_status = "Under Warranty" if self.coverage_type in (
				"In Warranty", "Partial Coverage"
			) else "Out of Warranty"
			sr.warranty_plan = self.warranty_plan
			sr.warranty_plan_name = frappe.db.get_value(
				"CH Warranty Plan", self.warranty_plan, "plan_name"
			) if self.warranty_plan else ""
			sr.warranty_expiry_date = self.warranty_end_date
			sr.warranty_deductible = flt(self.deductible_amount)

			# Issue details
			sr.issue_category = self.issue_category
			sr.issue_description = self.issue_description or ""

			# Estimate
			sr.estimated_cost = flt(self.estimated_repair_cost)

			# Link back to claim
			sr.internal_remarks = f"Auto-created from Warranty Claim {self.name}"

			# Fields required for SR submission
			sr.product_condition_desc = f"Warranty claim: {self.issue_description or 'Device issue reported'}"
			sr.backup_info = "N/A — warranty claim auto-created"

			sr.flags.ignore_permissions = True
			sr.flags.skip_warranty_fetch = True
			sr.insert()
			# Don't auto-submit — GoFix staff will verify details and submit

			# Update claim with SR reference
			old = self.claim_status
			self.service_request = sr.name
			self.claim_status = "Ticket Created"
			self.repair_status = "Pending"

			self.db_set({
				"service_request": sr.name,
				"claim_status": "Ticket Created",
				"repair_status": "Pending",
			})

			self._log("Ticket Created", old, "Ticket Created",
			          f"GoFix Service Request {sr.name} created",
			          save=not from_submit)

			# Update CH Serial Lifecycle to "In Service"
			self._update_lifecycle_in_service()

			frappe.msgprint(
				_("GoFix Service Request {0} created successfully").format(
					frappe.bold(sr.name)
				),
				indicator="green",
				alert=True,
			)

		except Exception as e:
			frappe.log_error(
				f"Failed to create GoFix ticket for claim {self.name}: {e}",
				"Warranty Claim - GoFix Ticket Error",
			)
			frappe.throw(
				_("Failed to create GoFix Service Request: {0}").format(str(e))
			)

	def _send_to_manufacturer(self, from_submit=False):
		"""Route claim to manufacturer — device goes to OEM for warranty repair."""
		old = self.claim_status
		self.claim_status = "Sent to Manufacturer"
		self.repair_status = "With Manufacturer"
		self.handover_date = self.handover_date or nowdate()

		self.db_set({
			"claim_status": "Sent to Manufacturer",
			"repair_status": "With Manufacturer",
			"handover_date": self.handover_date,
		})

		brand = self.brand or frappe.db.get_value("Item", self.item_code, "brand") or "manufacturer"
		self._log("Sent to Manufacturer", old, "Sent to Manufacturer",
		          f"Device sent to {brand} for warranty repair on behalf of customer",
		          save=not from_submit)

		# Update lifecycle
		self._update_lifecycle_in_service()

		frappe.msgprint(
			_("Device sent to {0} for manufacturer warranty repair").format(
				frappe.bold(brand)
			),
			indicator="blue",
			alert=True,
		)

	@frappe.whitelist()
	def send_to_manufacturer(self, remarks=None):
		"""Manually send device to manufacturer (if not auto-routed on submit)."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status not in ("Approved", "Pending Approval"):
			frappe.throw(_("Cannot send to manufacturer — current status: {0}").format(
				self.claim_status))

		self._send_to_manufacturer()

	def _update_lifecycle_in_service(self):
		"""Update CH Serial Lifecycle to In Service status."""
		if not frappe.db.exists("CH Serial Lifecycle", self.serial_no):
			return

		try:
			from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
				update_lifecycle_status,
			)
			update_lifecycle_status(
				serial_no=self.serial_no,
				new_status="In Service",
				company=GOFIX_COMPANY,
				remarks=f"Warranty Claim {self.name} — sent to GoFix for repair",
			)
		except Exception as e:
			frappe.log_error(
				f"Failed to update lifecycle to In Service for {self.serial_no}: {e}",
				"Warranty Claim - Lifecycle Update Error",
			)

	def _log(self, action, from_status, to_status, remarks="", save=True):
		"""Append a log entry to claim_log child table.

		Args:
			save: If False, just append (caller will save).
			      Set False when called from before_submit/on_submit hooks
			      since Frappe saves the doc automatically.
		"""
		self.append("claim_log", {
			"log_timestamp": now_datetime(),
			"action": action,
			"from_status": from_status,
			"to_status": to_status,
			"performed_by": frappe.session.user,
			"company": self.reported_at_company or self.company,
			"remarks": remarks,
		})
		if save:
			self.save(ignore_permissions=True)


# ── Whitelisted APIs ────────────────────────────────────────────────────────

@frappe.whitelist()
def get_warranty_claim_status(claim_name):
	"""Get current status of a warranty claim."""
	return frappe.db.get_value(
		"CH Warranty Claim", claim_name,
		["claim_status", "coverage_type", "approval_status", "repair_status",
		 "service_request", "settlement_status"],
		as_dict=True,
	)


@frappe.whitelist()
def get_claims_for_serial(serial_no):
	"""Get all warranty claims for a serial number."""
	return frappe.get_all(
		"CH Warranty Claim",
		filters={"serial_no": serial_no, "docstatus": ["!=", 2]},
		fields=[
			"name", "claim_date", "claim_status", "coverage_type",
			"issue_description", "service_request", "repair_status",
			"gogizmo_share", "customer_share",
		],
		order_by="creation desc",
	)
