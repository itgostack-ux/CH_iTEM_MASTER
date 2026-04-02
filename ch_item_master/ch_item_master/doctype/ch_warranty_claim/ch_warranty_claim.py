# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Warranty Claim — warranty/service claim lifecycle.

Revised flow (April 2026 — Coverage-based architecture):

Coverage types (priority order):
  1. repair_warranty   — GoFix part/service warranty from prior repair
  2. anniversary_warranty — long-duration extended warranty (GoGizmo)
  3. vas_plan           — short-term VAS/protection plans (GoFix or GoGizmo)
  4. manufacturer_warranty — OEM/brand warranty
  5. paid_repair        — no entitlement, customer pays full
  6. goodwill           — management-approved exception

Workflow:
  1. Draft → (submit) → Pending Coverage Check → Coverage Identified
  2. Coverage Decision Engine evaluates entitlement
  3. If approval needed: Pending Approval → Approved/Rejected/Need More Info
  4. Approved → Pickup or Walk-in receiving
  5. Device Received → Intake QC
  6. QC Passed → Processing Fee (ONLY after QC)
  7. Fee settled → GoFix Ticket Created (ONLY after all gates pass)
  8. Diagnosis → Additional Approval if needed → Repair
  9. Repair Complete → Final QC → Invoice → Payment → Delivery → Closed

Gate control for GoFix ticket:
  ALL required:
    - Claim approved (or auto-approved for no-liability claims)
    - Device physically received
    - Intake QC passed
    - Processing fee paid/waived/not required
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, now_datetime, getdate, flt

from buyback.utils import validate_indian_phone
from ch_item_master.ch_item_master.doctype.ch_vas_settings.ch_vas_settings import (
	get_vas_settings,
	get_warranty_company,
	get_service_company,
	get_fee_waiver_roles,
)


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
		self.pickup_required = 1 if self.mode_of_service in ("Pickup", "Courier") else 0
		self._set_title()
		self._set_reported_by()
		if not self.claim_status:
			self.claim_status = "Draft"

	def before_submit(self):
		self._lookup_warranty_coverage()
		self._run_coverage_decision_engine()
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
		if self.coverage_type == "manufacturer_warranty":
			if self.pickup_required:
				self.db_set({
					"claim_status": "Pickup Requested",
					"logistics_status": "Pickup Requested",
				})
				self._log(
					"Pickup Requested",
					"Approved",
					"Pickup Requested",
					"Awaiting pickup scheduling for manufacturer claim",
					save=False,
				)
			else:
				# Device goes to manufacturer, not GoFix
				self._send_to_manufacturer(from_submit=True)
		elif self.claim_status == "Approved":
			if self.pickup_required:
				self.db_set({
					"claim_status": "Pickup Requested",
					"logistics_status": "Pickup Requested",
				})
				self._log(
					"Pickup Requested",
					"Approved",
					"Pickup Requested",
					"Awaiting pickup scheduling for customer device",
					save=False,
				)
			# Walk-in: stays at Approved — device receiving happens next

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
		if self.claim_status not in ("Pending Approval", "Need More Information"):
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

		if self.pickup_required:
			self.db_set({
				"claim_status": "Pickup Requested",
				"logistics_status": "Pickup Requested",
			})
			self._log(
				"Pickup Requested",
				"Approved",
				"Pickup Requested",
				"Claim approved and waiting for pickup scheduling",
			)
		# Walk-in: stays at Approved — device receiving happens next

	@frappe.whitelist()
	def reject(self, reason=None):
		"""GoGizmo Head rejects the claim."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted before rejection."))
		if self.claim_status not in ("Pending Approval", "Need More Information"):
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
	def need_more_info(self, remarks=None):
		"""Claim manager requests additional information or photos."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status not in ("Pending Approval", "Approved"):
			frappe.throw(_("Cannot request more info — current status: {0}").format(
				self.claim_status))

		old = self.claim_status
		self.approval_status = "Need More Information"
		self.claim_status = "Need More Information"
		self.db_set({
			"approval_status": "Need More Information",
			"claim_status": "Need More Information",
		})
		self._log("Need More Information", old, "Need More Information",
		          remarks or f"More information requested by {frappe.session.user}")

	@frappe.whitelist()
	def request_additional_approval(self, additional_issue_description=None,
	                                 additional_cost_covered=0, additional_cost_customer=0,
	                                 additional_issue_photos=None, remarks=None):
		"""Technician/advisor found additional damage — request customer approval."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status not in ("Ticket Created", "In Repair"):
			frappe.throw(_("Additional approval requires active repair — current status: {0}").format(
				self.claim_status))

		old = self.claim_status
		updates = {
			"additional_issue_description": additional_issue_description or "",
			"additional_cost_covered": flt(additional_cost_covered),
			"additional_cost_customer": flt(additional_cost_customer),
			"additional_approval_status": "Pending",
			"claim_status": "Additional Approval Pending",
		}
		if additional_issue_photos:
			updates["additional_issue_photos"] = additional_issue_photos
		self.db_set(updates)

		self._log("Additional Approval Requested", old, "Additional Approval Pending",
		          remarks or f"Additional damage found: {additional_issue_description}")

		return {"claim_name": self.name, "claim_status": "Additional Approval Pending"}

	@frappe.whitelist()
	def resolve_additional_approval(self, decision, remarks=None):
		"""Record customer's decision on additional damage cost."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status != "Additional Approval Pending":
			frappe.throw(_("No additional approval pending — current status: {0}").format(
				self.claim_status))
		if decision not in ("Customer Approved", "Customer Rejected", "Override Approved"):
			frappe.throw(_("Decision must be Customer Approved, Customer Rejected, or Override Approved."))

		old = self.claim_status
		updates = {
			"additional_approval_status": decision,
			"additional_approval_decided_at": now_datetime(),
		}

		if decision in ("Customer Approved", "Override Approved"):
			# Add additional cost to customer share
			self.customer_share = flt(self.customer_share) + flt(self.additional_cost_customer)
			updates["customer_share"] = self.customer_share
			updates["claim_status"] = "In Repair"  # Resume repair
		else:
			updates["claim_status"] = "In Repair"  # Continue without additional work

		self.db_set(updates)
		self._log(f"Additional {decision}", old, updates["claim_status"],
		          remarks or f"Customer {decision.lower()} additional cost")

		return {"claim_name": self.name, "claim_status": updates["claim_status"]}

	@frappe.whitelist()
	def perform_final_qc(self, qc_result, qc_remarks=None):
		"""Final QC after repair is complete."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status != "Repair Complete":
			frappe.throw(_("Final QC requires repair complete — current status: {0}").format(
				self.claim_status))
		if qc_result not in ("Passed", "Failed"):
			frappe.throw(_("Final QC result must be Passed or Failed."))

		old = self.claim_status
		updates = {
			"final_qc_status": qc_result,
			"final_qc_by": frappe.session.user,
			"final_qc_at": now_datetime(),
			"final_qc_remarks": qc_remarks or "",
		}
		if qc_result == "Passed":
			updates["claim_status"] = "Final QC Passed"
		else:
			updates["claim_status"] = "Repair Complete"  # Back to repair

		self.db_set(updates)
		self._log(f"Final QC {qc_result}", old, updates["claim_status"],
		          qc_remarks or f"Final QC: {qc_result}")
		return {"claim_name": self.name, "claim_status": updates["claim_status"]}

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
	def schedule_pickup(self, pickup_address=None, pickup_slot=None,
	                   pickup_partner=None, pickup_tracking_no=None, remarks=None):
		"""Schedule customer pickup for claim device collection."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status in ("Closed", "Cancelled", "Rejected"):
			frappe.throw(_("Cannot schedule pickup — current status: {0}").format(self.claim_status))
		if not (pickup_address or self.pickup_address):
			frappe.throw(_("Pickup address is required."))

		old = self.claim_status
		updates = {
			"mode_of_service": "Pickup" if self.mode_of_service in (None, "", "Walk-in") else self.mode_of_service,
			"pickup_required": 1,
			"pickup_address": pickup_address or self.pickup_address,
			"pickup_slot": pickup_slot or self.pickup_slot,
			"pickup_partner": pickup_partner or self.pickup_partner,
			"pickup_tracking_no": pickup_tracking_no or self.pickup_tracking_no,
			"pickup_scheduled_at": now_datetime(),
			"logistics_status": "Pickup Scheduled",
			"claim_status": "Pickup Scheduled",
		}
		self.db_set(updates)

		self._log(
			"Pickup Scheduled",
			old,
			"Pickup Scheduled",
			remarks or _("Pickup scheduled for customer device"),
		)

		return {
			"claim_name": self.name,
			"claim_status": "Pickup Scheduled",
			"logistics_status": "Pickup Scheduled",
			"pickup_scheduled_at": updates["pickup_scheduled_at"],
		}

	@frappe.whitelist()
	def mark_picked_up(self, delivery_otp=None, remarks=None):
		"""Mark device as picked up from customer location."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status not in ("Pickup Requested", "Pickup Scheduled", "Approved"):
			frappe.throw(_("Cannot mark picked up — current status: {0}").format(self.claim_status))

		old = self.claim_status
		updates = {
			"picked_up_at": now_datetime(),
			"delivery_otp": delivery_otp or self.delivery_otp,
			"logistics_status": "Picked Up",
			"claim_status": "Picked Up",
		}
		self.db_set(updates)
		self._log("Picked Up", old, "Picked Up", remarks or _("Device picked up from customer"))

		if self.coverage_type == "Manufacturer Warranty":
			self.reload()
			if self.claim_status != "Sent to Manufacturer":
				self._send_to_manufacturer()
		# For non-manufacturer claims: device goes through receiving → QC → fee → ticket
		# Do NOT auto-create GoFix ticket here

		self.reload()
		return {
			"claim_name": self.name,
			"claim_status": self.claim_status,
			"logistics_status": self.logistics_status,
		}

	@frappe.whitelist()
	def mark_out_for_delivery(self, pickup_partner=None, pickup_tracking_no=None, remarks=None):
		"""Mark repaired device as out for customer delivery."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status != "Repair Complete":
			frappe.throw(_("Cannot mark out for delivery — current status: {0}").format(self.claim_status))

		old = self.claim_status
		updates = {
			"out_for_delivery_at": now_datetime(),
			"pickup_partner": pickup_partner or self.pickup_partner,
			"pickup_tracking_no": pickup_tracking_no or self.pickup_tracking_no,
			"logistics_status": "Out for Delivery",
			"claim_status": "Out for Delivery",
		}
		self.db_set(updates)
		self._log(
			"Out for Delivery",
			old,
			"Out for Delivery",
			remarks or _("Device dispatched for customer delivery"),
		)

		return {
			"claim_name": self.name,
			"claim_status": "Out for Delivery",
			"logistics_status": "Out for Delivery",
		}

	@frappe.whitelist()
	def mark_delivered_back(self, delivery_otp=None, remarks=None):
		"""Mark final handover to customer complete."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status not in ("Out for Delivery", "Repair Complete"):
			frappe.throw(_("Cannot mark delivered — current status: {0}").format(self.claim_status))

		old = self.claim_status
		updates = {
			"delivered_back_at": now_datetime(),
			"delivery_otp": delivery_otp or self.delivery_otp,
			"logistics_status": "Delivered",
			"claim_status": "Delivered",
		}
		self.db_set(updates)
		self._log("Delivered", old, "Delivered", remarks or _("Device delivered to customer"))

		return {
			"claim_name": self.name,
			"claim_status": "Delivered",
			"logistics_status": "Delivered",
		}

	@frappe.whitelist()
	def close_claim(self, remarks=None):
		"""Close the claim after settlement — sets final_outcome."""
		if self.claim_status not in (
			"Repair Complete", "Approved", "Rejected", "Sent to Manufacturer",
			"Delivered", "QC Failed", "Final QC Passed", "Payment Received", "Not Repairable"
		):
			frappe.throw(_("Cannot close — current status: {0}").format(
				self.claim_status))

		old = self.claim_status

		# ── Calculate total claim cost ────────────────────────────────────
		self.total_claim_cost = (
			flt(self.estimated_repair_cost)
			+ flt(self.logistics_cost)
			+ flt(self.third_party_repair_cost)
			+ flt(self.additional_cost_covered)
		)

		# ── Determine final outcome ──────────────────────────────────────
		final_outcome = self._determine_final_outcome(old)

		self.claim_status = "Closed"
		self.settlement_status = "Settled"

		update_dict = {
			"claim_status": "Closed",
			"settlement_status": "Settled",
			"total_claim_cost": self.total_claim_cost,
			"final_outcome": final_outcome,
		}
		if remarks:
			update_dict["final_outcome_remarks"] = remarks

		self.db_set(update_dict)

		self._log("Closed", old, "Closed",
		          f"Final outcome: {final_outcome}. " + (remarks or ""))

		# Record claim in service warranty register (for repair warranty tracking)
		if self.coverage_type in ("vas_plan", "anniversary_warranty", "paid_repair"):
			self._create_post_repair_warranty()

		# Increment claims_used on the sold plan
		if self.sold_plan:
			try:
				sp = frappe.get_doc("CH Sold Plan", self.sold_plan)
				sp.record_claim(
					service_reference=self.name,
					claim_cost=flt(self.total_claim_cost),
				)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"Sold Plan claim increment failed for {self.name}",
				)

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
					remarks=f"Claim closed — {final_outcome} — {self.issue_description or ''}",
				)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"VAS Ledger log failed for claim {self.name}",
				)

	def _determine_final_outcome(self, closing_from_status):
		"""Map coverage_type + closing status to final outcome category."""
		if closing_from_status == "Rejected":
			return "Rejected Claim"
		if closing_from_status in ("QC Failed", "Not Repairable"):
			if self.repair_status == "Not Repairable":
				return "Not Repairable at Intake"
			return "Not Repairable at Intake"

		outcome_map = {
			"anniversary_warranty": "Repaired Under Anniversary Warranty",
			"vas_plan": "Repaired Under VAS Plan",
			"repair_warranty": "Repaired Under Repair Warranty",
			"paid_repair": "Repaired Paid",
			"goodwill": "Goodwill Repair",
			"manufacturer_warranty": "Repaired Under Anniversary Warranty",  # fallback
		}
		return outcome_map.get(self.coverage_type, "Repaired Paid")

	def _create_post_repair_warranty(self):
		"""After repair closes, create a Service Warranty Register entry for future claims."""
		try:
			from ch_item_master.ch_item_master.doctype.ch_service_warranty_register.ch_service_warranty_register import (
				create_service_warranty,
			)
			# Only create if a GoFix service was performed
			if not self.service_request:
				return

			# Determine warranty months: plan override > VAS Settings default
			warranty_months = 0
			if self.warranty_plan:
				warranty_months = frappe.db.get_value(
					"CH Warranty Plan", self.warranty_plan, "post_repair_warranty_months"
				) or 0
			if not warranty_months:
				warranty_months = get_vas_settings().post_repair_warranty_months or 3

			create_service_warranty(
				service_invoice=None,  # Will be linked when invoice is created
				service_ticket=self.service_request,
				serial_no=self.serial_no,
				item_code=self.item_code,
				customer=self.customer,
				part_replaced=self.issue_description[:100] if self.issue_description else "Device Repair",
				warranty_months=warranty_months,
				service_date=self.repair_completion_date or nowdate(),
				service_company=get_service_company(),
				claim_name=self.name,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
			                 f"Post-repair warranty creation failed for {self.name}")

	# ── Device Receiving ─────────────────────────────────────────────────

	@frappe.whitelist()
	def mark_device_received(self, condition_on_receipt=None, accessories_received=None,
	                         imei_verified=0, receiving_remarks=None):
		"""Record physical receipt of device at store/service center.

		Valid from: Approved (walk-in), Picked Up (after pickup)
		"""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status not in ("Approved", "Picked Up"):
			frappe.throw(_("Cannot receive device — current status: {0}. "
			               "Device can only be received after approval or pickup.").format(
				self.claim_status))

		old = self.claim_status
		updates = {
			"device_received_at": now_datetime(),
			"device_received_by": frappe.session.user,
			"condition_on_receipt": condition_on_receipt or "",
			"accessories_received": accessories_received or "",
			"imei_verified": 1 if imei_verified else 0,
			"receiving_remarks": receiving_remarks or "",
			"claim_status": "Device Received",
			"logistics_status": "Device Received",
			"intake_qc_status": "Pending",
		}
		self.db_set(updates)
		self._log("Device Received", old, "Device Received",
		          receiving_remarks or _("Device received at store/service center"))

		return {
			"claim_name": self.name,
			"claim_status": "Device Received",
			"intake_qc_status": "Pending",
		}

	# ── Intake QC ────────────────────────────────────────────────────────

	@frappe.whitelist()
	def perform_intake_qc(self, qc_result, qc_remarks=None, qc_result_reason=None,
	                      qc_checks=None):
		"""Perform mandatory intake QC after device receipt.

		Args:
			qc_result: 'Passed' | 'Failed' | 'Not Repairable'
			qc_remarks: General QC notes
			qc_result_reason: Mandatory reason if Failed/Not Repairable
			qc_checks: list of dicts for QC Checklist rows
		"""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.claim_status not in ("Device Received", "QC Pending"):
			frappe.throw(_("Cannot perform QC — current status: {0}. "
			               "Device must be received first.").format(self.claim_status))

		if qc_result not in ("Passed", "Failed", "Not Repairable"):
			frappe.throw(_("QC result must be Passed, Failed, or Not Repairable."))

		if qc_result in ("Failed", "Not Repairable") and not qc_result_reason:
			frappe.throw(_("Reason is mandatory when QC result is {0}.").format(qc_result))

		old = self.claim_status
		updates = {
			"intake_qc_status": qc_result,
			"intake_qc_by": frappe.session.user,
			"intake_qc_at": now_datetime(),
			"intake_qc_remarks": qc_remarks or "",
			"intake_qc_result_reason": qc_result_reason or "",
		}

		if qc_result == "Passed":
			updates["claim_status"] = "QC Passed"
		elif qc_result == "Failed":
			updates["claim_status"] = "QC Failed"
		else:  # Not Repairable
			updates["claim_status"] = "QC Failed"
			updates["repair_status"] = "Not Repairable"

		self.db_set(updates)

		# Add QC checklist rows if provided
		if qc_checks:
			import json as _json
			if isinstance(qc_checks, str):
				qc_checks = _json.loads(qc_checks)
			for check in qc_checks:
				self.append("intake_qc_checks", {
					"check_name": check.get("check_name", ""),
					"result": check.get("result", ""),
					"photo": check.get("photo", ""),
					"remarks": check.get("remarks", ""),
				})
			self.save(ignore_permissions=True)

		self._log(f"QC {qc_result}", old, updates["claim_status"],
		          qc_remarks or f"Intake QC: {qc_result}" + (
		              f" — {qc_result_reason}" if qc_result_reason else ""))

		return {
			"claim_name": self.name,
			"claim_status": updates["claim_status"],
			"intake_qc_status": qc_result,
		}

	# ── Processing Fee ───────────────────────────────────────────────────

	@frappe.whitelist()
	def generate_processing_fee(self, fee_amount=None):
		"""Calculate and set processing fee AFTER QC passes.

		Fee is mandatory. Only the amount can be overridden.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.intake_qc_status != "Passed":
			frappe.throw(_("Processing fee can only be set after intake QC passes."))
		if self.claim_status not in ("QC Passed", "Fee Pending"):
			frappe.throw(_("Cannot set fee — current status: {0}").format(self.claim_status))

		amount = flt(fee_amount) if fee_amount is not None else self._calculate_processing_fee()

		updates = {
			"processing_fee_amount": amount,
			"processing_fee_status": "Pending" if amount > 0 else "Not Required",
			"claim_status": "Fee Pending" if amount > 0 else "Fee Paid",
		}
		self.db_set(updates)

		if amount > 0:
			self._log("Fee Generated", self.claim_status, "Fee Pending",
			          f"Processing fee ₹{amount} determined after intake QC")
		else:
			self._log("Fee Not Required", self.claim_status, updates["claim_status"],
			          "No processing fee required for this claim")

		return {
			"claim_name": self.name,
			"claim_status": updates["claim_status"],
			"processing_fee_amount": amount,
			"processing_fee_status": updates["processing_fee_status"],
		}

	@frappe.whitelist()
	def send_fee_payment_link(self, channel="WhatsApp"):
		"""Generate and send payment link to customer for processing fee.

		Args:
			channel: 'WhatsApp' | 'SMS' | 'Email'
		"""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.processing_fee_status not in ("Pending", "Link Sent"):
			frappe.throw(_("Fee is not pending — current status: {0}").format(
				self.processing_fee_status))
		if flt(self.processing_fee_amount) <= 0:
			frappe.throw(_("No processing fee amount set."))

		# Generate payment link URL (can be replaced with actual payment gateway)
		base_url = frappe.utils.get_url()
		link_url = (f"{base_url}/api/method/ch_item_master.ch_item_master.warranty_api"
		            f".pay_processing_fee?claim={self.name}&amount={self.processing_fee_amount}")

		updates = {
			"processing_fee_link_url": link_url,
			"processing_fee_link_sent_at": now_datetime(),
			"processing_fee_link_sent_via": channel,
			"processing_fee_status": "Link Sent",
		}
		self.db_set(updates)

		self._log("Payment Link Sent", self.claim_status, self.claim_status,
		          f"Payment link sent via {channel} for ₹{self.processing_fee_amount}")

		# TODO: Actual WhatsApp/SMS integration
		# For now, just record the link generation
		frappe.msgprint(
			_("Payment link generated: ₹{0} via {1}").format(
				self.processing_fee_amount, channel),
			indicator="blue", alert=True,
		)

		return {
			"claim_name": self.name,
			"link_url": link_url,
			"channel": channel,
			"processing_fee_status": "Link Sent",
		}

	@frappe.whitelist()
	def mark_fee_paid(self, paid_amount=None, payment_mode=None,
	                  payment_ref=None, remarks=None):
		"""Record processing fee payment from customer."""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.processing_fee_status in ("Paid", "Waived", "Not Required"):
			frappe.throw(_("Fee already settled — status: {0}").format(
				self.processing_fee_status))

		amount = flt(paid_amount) or flt(self.processing_fee_amount)
		old = self.claim_status

		updates = {
			"processing_fee_status": "Paid",
			"processing_fee_paid_at": now_datetime(),
			"processing_fee_paid_amount": amount,
			"processing_fee_payment_mode": payment_mode or "",
			"processing_fee_payment_ref": payment_ref or "",
			"claim_status": "Fee Paid",
		}
		self.db_set(updates)

		self._log("Fee Paid", old, "Fee Paid",
		          remarks or f"Processing fee ₹{amount} paid via {payment_mode or 'N/A'}")

		return {
			"claim_name": self.name,
			"claim_status": "Fee Paid",
			"processing_fee_status": "Paid",
		}

	@frappe.whitelist()
	def waive_processing_fee(self, waiver_reason, waived_amount=None):
		"""Request fee waiver — requires manager approval.

		Store executive provides reason; manager role must approve.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))
		if self.processing_fee_status in ("Paid", "Waived", "Not Required"):
			frappe.throw(_("Fee already settled — status: {0}").format(
				self.processing_fee_status))
		if not waiver_reason:
			frappe.throw(_("Waiver reason is mandatory."))

		amount = flt(waived_amount) or flt(self.processing_fee_amount)
		old = self.claim_status

		# Check if user has manager role for direct approval
		waiver_roles = get_fee_waiver_roles()
		is_manager = frappe.db.exists("Has Role", {
			"parent": frappe.session.user,
			"role": ["in", waiver_roles],
		}) if waiver_roles else False

		if is_manager:
			updates = {
				"processing_fee_status": "Waived",
				"processing_fee_waived_amount": amount,
				"processing_fee_waiver_reason": waiver_reason,
				"processing_fee_waiver_approved_by": frappe.session.user,
				"processing_fee_waiver_approved_at": now_datetime(),
				"claim_status": "Fee Waived",
			}
			self.db_set(updates)
			self._log("Fee Waived", old, "Fee Waived",
			          f"₹{amount} waived by {frappe.session.user}: {waiver_reason}")

			return {
				"claim_name": self.name,
				"claim_status": "Fee Waived",
				"processing_fee_status": "Waived",
				"approved": True,
			}
		else:
			# Create exception request for manager approval
			self._create_exception_request(
				"Processing Fee Waiver",
				f"Fee waiver ₹{amount} requested: {waiver_reason}",
				requested_value=amount,
				original_value=flt(self.processing_fee_amount),
			)
			self._log("Fee Waiver Requested", old, old,
			          f"Waiver ₹{amount} requested by {frappe.session.user}: {waiver_reason}")

			return {
				"claim_name": self.name,
				"claim_status": old,
				"processing_fee_status": self.processing_fee_status,
				"approved": False,
				"message": _("Waiver request submitted for manager approval."),
			}

	# ── Create Repair Ticket (manual trigger with strict gate) ───────

	@frappe.whitelist()
	def create_repair_ticket(self, remarks=None):
		"""Manually trigger GoFix ticket creation with ALL gate checks.

		This is the ONLY way to create a repair ticket. All automated
		ticket creation has been removed.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Claim must be submitted first."))

		# Strict gate control
		errors = []
		if self.claim_status in ("Pending Approval", "Draft", "Rejected", "Cancelled"):
			errors.append(_("Claim must be approved (current: {0})").format(self.claim_status))
		if not self.device_received_at:
			errors.append(_("Device must be physically received at store"))
		if self.intake_qc_status != "Passed":
			errors.append(_("Intake QC must pass (current: {0})").format(
				self.intake_qc_status or "Not Done"))
		if (self.processing_fee_status not in ("Paid", "Waived", "Not Required")
				and flt(self.processing_fee_amount) > 0):
			errors.append(_("Processing fee must be paid or waived (current: {0})").format(
				self.processing_fee_status))
		if self.service_request:
			errors.append(_("GoFix ticket {0} already exists").format(self.service_request))

		if errors:
			frappe.throw(
				_("Cannot create repair ticket:") + "<br>" + "<br>".join(
					f"• {e}" for e in errors),
				title=_("Gate Check Failed"),
			)

		self._create_gofix_ticket()

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
		"""Legacy wrapper — calls new engine."""
		self._run_coverage_decision_engine()

	def _run_coverage_decision_engine(self):
		"""Coverage Decision Engine — evaluates all entitlements by priority.

		Priority: repair_warranty > anniversary_warranty > vas_plan >
		          manufacturer_warranty > paid_repair > goodwill

		Stores decision trace in CH Warranty Decision Log.
		"""
		import json as _json

		trace = []  # audit trail
		alternatives = []  # why lower-priority not selected
		selected = None

		# ── 1. Check repair_warranty (highest priority) ──
		swr_match = self._check_repair_warranty(trace)
		if swr_match:
			selected = {
				"coverage_type": "repair_warranty",
				"coverage_source": "gofix",
				"coverage_priority": 1,
				"reference_doctype": "CH Service Warranty Register",
				"reference_id": swr_match.get("name"),
				"entitlement_decision": "Eligible",
				"entitlement_reason": (
					f"Active GoFix repair warranty: {swr_match.get('part_replaced', 'Service')} "
					f"from {swr_match.get('service_date')}, valid till {swr_match.get('warranty_end_date')}"
				),
			}
			trace.append({"step": "repair_warranty", "result": "SELECTED", "record": swr_match.get("name")})

		# ── 2. Check anniversary_warranty ──
		if not selected and self.sold_plan:
			plan_type = self.plan_type or frappe.db.get_value("CH Sold Plan", self.sold_plan, "plan_type")
			if plan_type in ("Own Warranty", "Extended") and self.warranty_status == "Under Warranty":
				company = frappe.db.get_value("CH Sold Plan", self.sold_plan, "company") if self.sold_plan else ""

				# Check plan-level coverage_type_override first
				plan_coverage_override = ""
				if self.warranty_plan:
					plan_coverage_override = frappe.db.get_value(
						"CH Warranty Plan", self.warranty_plan, "coverage_type_override"
					) or ""

				if plan_coverage_override == "anniversary_warranty":
					is_anniversary = True
				elif plan_coverage_override == "vas_plan":
					is_anniversary = False
				else:
					# Auto-detect from duration vs threshold
					duration = frappe.db.get_value(
						"CH Warranty Plan", self.warranty_plan, "duration_months"
					) or 0
					settings = get_vas_settings()
					threshold = settings.anniversary_threshold_months or 24
					is_anniversary = duration >= threshold

				if is_anniversary:
					selected = {
						"coverage_type": "anniversary_warranty",
						"coverage_source": "gogizmo" if company == get_warranty_company() else "gofix",
						"coverage_priority": 2,
						"reference_doctype": "CH Sold Plan",
						"reference_id": self.sold_plan,
						"entitlement_decision": "Eligible",
						"entitlement_reason": (
							f"Anniversary warranty: {self.warranty_plan}, "
							f"valid {self.warranty_start_date} to {self.warranty_end_date}, "
							f"claims {self.claims_used}/{self.max_claims}"
						),
					}
					trace.append({"step": "anniversary_warranty", "result": "SELECTED", "plan": self.sold_plan})
				else:
					alternatives.append({
						"type": "anniversary_warranty", "reason": f"Plan duration {duration}m < 24m, classified as VAS"
					})
					trace.append({"step": "anniversary_warranty", "result": "SKIP", "reason": f"duration {duration}m < 24m"})

		# ── 3. Check vas_plan ──
		if not selected and self.sold_plan and self.warranty_status == "Under Warranty":
			plan_type = self.plan_type or frappe.db.get_value("CH Sold Plan", self.sold_plan, "plan_type")
			company = frappe.db.get_value("CH Sold Plan", self.sold_plan, "company") if self.sold_plan else ""
			if plan_type in ("Value Added Service", "VAS", "Protection", "Post-Repair Warranty", "Extended", "Own Warranty"):
				selected = {
					"coverage_type": "vas_plan",
					"coverage_source": "gogizmo" if company == get_warranty_company() else "gofix",
					"coverage_priority": 3,
					"reference_doctype": "CH Sold Plan",
					"reference_id": self.sold_plan,
					"entitlement_decision": "Eligible",
					"entitlement_reason": (
						f"VAS plan: {self.warranty_plan} ({plan_type}), "
						f"valid {self.warranty_start_date} to {self.warranty_end_date}"
					),
				}
				trace.append({"step": "vas_plan", "result": "SELECTED", "plan": self.sold_plan, "type": plan_type})
		elif not selected:
			alternatives.append({
				"type": "vas_plan", "reason": f"No active sold plan or warranty_status={self.warranty_status}"
			})
			trace.append({"step": "vas_plan", "result": "SKIP", "reason": "no active plan"})

		# ── 4. Check manufacturer_warranty ──
		if not selected and getattr(self.flags, "is_manufacturer_warranty", False):
			selected = {
				"coverage_type": "manufacturer_warranty",
				"coverage_source": "oem",
				"coverage_priority": 4,
				"reference_doctype": "",
				"reference_id": "",
				"entitlement_decision": "Eligible",
				"entitlement_reason": (
					f"OEM/manufacturer warranty active till {self.warranty_end_date}"
				),
			}
			trace.append({"step": "manufacturer_warranty", "result": "SELECTED"})

		# ── 5. Default: paid_repair ──
		if not selected:
			selected = {
				"coverage_type": "paid_repair",
				"coverage_source": "",
				"coverage_priority": 5,
				"reference_doctype": "",
				"reference_id": "",
				"entitlement_decision": "Not Eligible",
				"entitlement_reason": "No active warranty or plan covers this device/issue",
			}
			trace.append({"step": "paid_repair", "result": "DEFAULT"})

		# ── Apply decision ──
		self.coverage_type = selected["coverage_type"]
		self.coverage_source = selected["coverage_source"]
		self.coverage_priority = selected["coverage_priority"]
		self.coverage_reference_doctype = selected.get("reference_doctype") or ""
		self.coverage_reference_id = selected.get("reference_id") or ""
		self.entitlement_decision = selected["entitlement_decision"]
		self.entitlement_reason = selected["entitlement_reason"]

		# For repair warranty, populate previous service fields
		if selected["coverage_type"] == "repair_warranty" and swr_match:
			self.previous_service_invoice = swr_match.get("service_invoice")
			self.previous_service_ticket = swr_match.get("service_ticket")
			self.previous_service_date = swr_match.get("service_date")
			self.replaced_part_reference = swr_match.get("part_replaced")
			self.part_warranty_valid_till = swr_match.get("warranty_end_date")
			self.service_warranty_register = swr_match.get("name")

		# ── Log decision ──
		try:
			frappe.get_doc({
				"doctype": "CH Warranty Decision Log",
				"claim": self.name,
				"serial_no": self.serial_no,
				"evaluated_at": now_datetime(),
				"evaluated_by": frappe.session.user,
				"selected_coverage_type": selected["coverage_type"],
				"selected_coverage_source": selected["coverage_source"],
				"selected_priority": selected["coverage_priority"],
				"coverage_reference_doctype": selected.get("reference_doctype") or "",
				"coverage_reference_id": selected.get("reference_id") or "",
				"entitlement_decision": selected["entitlement_decision"],
				"entitlement_reason": selected["entitlement_reason"],
				"decision_trace": _json.dumps(trace, default=str),
				"alternatives_checked": _json.dumps(alternatives, default=str),
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Decision log failed for {self.name}")

	def _check_repair_warranty(self, trace):
		"""Check if device has an active GoFix repair warranty that covers this issue.

		Returns dict of matching SWR record, or None.
		"""
		if not self.serial_no:
			trace.append({"step": "repair_warranty", "result": "SKIP", "reason": "no serial_no"})
			return None

		# Find active repair warranties for this device
		swrs = frappe.get_all(
			"CH Service Warranty Register",
			filters={
				"serial_no": self.serial_no,
				"status": "Active",
				"warranty_end_date": [">=", nowdate()],
			},
			fields=["name", "service_invoice", "service_ticket", "service_date",
			         "part_replaced", "part_item_code", "warranty_start_date",
			         "warranty_end_date", "covered_issue_categories",
			         "excluded_issue_categories", "claims_used", "max_claims",
			         "service_type", "warranty_months"],
			order_by="warranty_end_date desc",
		)

		if not swrs:
			trace.append({"step": "repair_warranty", "result": "SKIP", "reason": "no active SWR records"})
			return None

		# Get issue categories from claim
		issue_cats = []
		if self.issue_category:
			issue_cats.append(self.issue_category)

		# Check each SWR for issue match
		for swr in swrs:
			if int(swr.get("claims_used") or 0) >= int(swr.get("max_claims") or 1):
				trace.append({
					"step": "repair_warranty", "record": swr["name"],
					"result": "SKIP", "reason": "claims exhausted"
				})
				continue

			# Check issue category match
			covered = [c.strip().lower() for c in (swr.get("covered_issue_categories") or "").split(",") if c.strip()]
			excluded = [c.strip().lower() for c in (swr.get("excluded_issue_categories") or "").split(",") if c.strip()]

			# Check exclusions
			excluded_match = False
			for cat in issue_cats:
				if cat.lower().strip() in excluded:
					excluded_match = True
					break

			if excluded_match:
				trace.append({
					"step": "repair_warranty", "record": swr["name"],
					"result": "SKIP", "reason": f"issue category excluded"
				})
				continue

			# If covered categories specified, check match
			if covered and issue_cats:
				matched = any(cat.lower().strip() in covered for cat in issue_cats)
				if not matched:
					trace.append({
						"step": "repair_warranty", "record": swr["name"],
						"result": "SKIP", "reason": "issue category not in covered list"
					})
					continue

			# Match found!
			trace.append({
				"step": "repair_warranty", "record": swr["name"],
				"result": "MATCH", "part": swr.get("part_replaced"),
				"valid_till": str(swr.get("warranty_end_date")),
			})
			return swr

		return None

	def _calculate_cost_split(self):
		"""Calculate how much each party pays based on coverage_type and plan config.

		Uses company_share_percent from plan (or coverage rule override) to
		determine the split. Defaults to 100% company share for warranty plans.
		"""
		est = flt(self.estimated_repair_cost)

		if self.coverage_type == "manufacturer_warranty":
			self.gogizmo_share = 0
			self.gofix_share = 0
			self.customer_share = 0
		elif self.coverage_type == "repair_warranty":
			self.gogizmo_share = 0
			self.gofix_share = est
			self.customer_share = 0
		elif self.coverage_type in ("anniversary_warranty", "vas_plan"):
			# Get company share percent from plan config
			company_pct = self._get_company_share_percent()
			deductible = flt(self.deductible_amount)
			after_deductible = max(0, est - deductible)
			self.gogizmo_share = flt(after_deductible * company_pct / 100, 2)
			self.gofix_share = 0
			self.customer_share = flt(est - self.gogizmo_share, 2)
		elif self.coverage_type == "goodwill":
			self.gogizmo_share = est
			self.gofix_share = 0
			self.customer_share = 0
		else:
			# paid_repair
			self.gogizmo_share = 0
			self.gofix_share = 0
			self.customer_share = est

	def _get_company_share_percent(self):
		"""Get company share % from coverage rule (per-issue) or plan, default 100."""
		# Check per-issue override first
		if self.issue_category and self.warranty_plan:
			plan = frappe.get_cached_doc("CH Warranty Plan", self.warranty_plan)
			for rule in (plan.coverage_rules or []):
				if rule.issue_type == self.issue_category:
					if rule.company_share_percent is not None and flt(rule.company_share_percent) > 0:
						return flt(rule.company_share_percent)
					break

		# Plan-level override
		if self.warranty_plan:
			plan_pct = frappe.db.get_value(
				"CH Warranty Plan", self.warranty_plan, "company_share_percent"
			)
			if plan_pct is not None and flt(plan_pct) > 0:
				return flt(plan_pct)

		return 100  # default: company absorbs 100% after deductible

	def _calculate_processing_fee(self):
		"""Calculate processing fee from plan fee rules or VAS Settings defaults.

		Priority:
		  1. CH Plan Fee Rule child table on warranty plan (if configured)
		  2. VAS Settings defaults (for paid_repair / legacy plans)
		  3. Hardcoded zero for manufacturer/repair/goodwill
		"""
		import json as _json

		if self.coverage_type in ("manufacturer_warranty", "repair_warranty", "goodwill"):
			return 0

		# Check plan fee rules first
		if self.warranty_plan:
			plan = frappe.get_cached_doc("CH Warranty Plan", self.warranty_plan)
			fee_rules = plan.fee_rules or []
			# Filter by applicable service mode
			applicable_rules = self._filter_applicable_fee_rules(fee_rules)
			if applicable_rules:
				return self._compute_fee_from_rules(applicable_rules)

		# No plan fee rules → use defaults
		if self.coverage_type in ("anniversary_warranty", "vas_plan"):
			return flt(self.deductible_amount)

		# paid_repair — VAS Settings defaults
		settings = get_vas_settings()
		pct = flt(settings.paid_repair_fee_percent) or 10
		minimum = flt(settings.paid_repair_fee_minimum) or 200
		maximum = flt(settings.paid_repair_fee_maximum)
		fee = flt(self.estimated_repair_cost) * pct / 100
		fee = max(minimum, fee)
		if maximum > 0:
			fee = min(maximum, fee)
		return flt(fee, 0)

	def _filter_applicable_fee_rules(self, fee_rules):
		"""Filter fee rules by applicable service mode for the current claim."""
		result = []
		for rule in fee_rules:
			if rule.applicable_modes:
				modes = [m.strip() for m in (rule.applicable_modes or "").split(",") if m.strip()]
				if modes and self.mode_of_service not in modes:
					continue
			result.append(rule)
		return result

	def _compute_fee_from_rules(self, rules):
		"""Compute total fee from multiple CH Plan Fee Rule rows.

		Stores breakdown in self.fee_breakdown for audit.
		"""
		import json as _json

		total = 0
		breakdown = []
		est = flt(self.estimated_repair_cost)

		for rule in rules:
			amount = 0
			if rule.fee_mode == "Fixed Amount":
				amount = flt(rule.fee_amount)
			elif rule.fee_mode == "Percentage of Estimate":
				amount = est * flt(rule.fee_percent) / 100
			elif rule.fee_mode == "Plan Deductible":
				amount = flt(self.deductible_amount)

			# Apply min/max
			if flt(rule.fee_minimum) > 0:
				amount = max(flt(rule.fee_minimum), amount)
			if flt(rule.fee_maximum) > 0:
				amount = min(flt(rule.fee_maximum), amount)

			amount = flt(amount, 2)
			total += amount
			breakdown.append({
				"fee_type": rule.fee_type,
				"fee_mode": rule.fee_mode,
				"amount": amount,
				"when_to_collect": rule.when_to_collect,
			})

		# Store breakdown for audit
		try:
			self.db_set("fee_breakdown", _json.dumps(breakdown, default=str))
		except Exception:
			pass

		return flt(total, 0)

	def _check_approval_needed(self):
		"""Determine if centralized approval is needed.

		Uses plan-level requires_approval setting with fallback to coverage-type rules.
		Plan setting overrides: Always, Never, If Company Pays, Auto (use defaults).
		"""
		plan_setting = ""
		if self.warranty_plan:
			plan_setting = frappe.db.get_value(
				"CH Warranty Plan", self.warranty_plan, "requires_approval"
			) or "Auto"

		# Auto-approve threshold from VAS Settings
		settings = get_vas_settings()
		threshold = flt(settings.auto_approve_threshold)

		if plan_setting == "Always":
			self.requires_approval = 1
		elif plan_setting == "Never":
			self.requires_approval = 0
		elif plan_setting == "If Company Pays":
			self.requires_approval = 1 if flt(self.gogizmo_share) > 0 else 0
		else:
			# Auto — use coverage-type defaults
			if self.coverage_type == "repair_warranty":
				self.requires_approval = 0
			elif self.coverage_type == "manufacturer_warranty":
				self.requires_approval = 0
			elif self.coverage_type == "paid_repair":
				self.requires_approval = 0
			elif self.coverage_type == "anniversary_warranty":
				self.requires_approval = 1
			elif self.coverage_type == "goodwill":
				self.requires_approval = 1
			else:
				# vas_plan — requires approval if GoGizmo bears cost
				self.requires_approval = 1 if flt(self.gogizmo_share) > 0 else 0

		# Auto-approve threshold override
		if self.requires_approval and threshold > 0 and flt(self.gogizmo_share) <= threshold:
			self.requires_approval = 0

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
		"""Create a GoFix Service Request from this claim.

		STRICT GATE: requires ALL preconditions met.
		"""
		if self.service_request:
			return  # Already created — prevent duplicates

		# ── Full gate control (backend enforcement) ──────────────────────
		self.reload()  # Ensure latest field values

		gate_errors = []
		if self.approval_status not in ("Approved", "") or self.claim_status in (
				"Draft", "Pending Approval", "Rejected", "Cancelled"):
			gate_errors.append(_("Claim not approved"))
		if not self.device_received_at:
			gate_errors.append(_("Device not received at store"))
		if self.intake_qc_status != "Passed":
			gate_errors.append(_("Intake QC not passed (status: {0})").format(
				self.intake_qc_status or "Not Done"))
		if (self.processing_fee_status not in ("Paid", "Waived", "Not Required")
				and flt(self.processing_fee_amount) > 0):
			gate_errors.append(_("Processing fee not cleared (status: {0}, amount: ₹{1})").format(
				self.processing_fee_status, self.processing_fee_amount))

		if gate_errors:
			frappe.throw(
				_("Cannot create repair ticket — gate check failed:") + "<br>"
				+ "<br>".join(f"• {e}" for e in gate_errors),
				title=_("Repair Gate Control"),
			)

		try:
			# Get GoFix warehouse
			gofix_warehouse = frappe.db.get_value(
				"Warehouse", {"company": get_service_company(), "is_group": 0},
				"name", order_by="creation asc"
			) or ""

			sr = frappe.new_doc("Service Request")
			sr.customer = self.customer
			sr.customer_name = self.customer_name
			sr.contact_number = self.customer_phone or ""
			sr.company = get_service_company()
			sr.device_item = self.item_code
			sr.serial_no = self.serial_no
			sr.brand = self.brand
			sr.source_warehouse = gofix_warehouse
			sr.service_date = nowdate()

			# Warranty info
			sr.warranty_status = "Under Warranty" if self.coverage_type in (
				"anniversary_warranty", "vas_plan", "repair_warranty", "goodwill"
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
				company=get_service_company(),
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
		 "service_request", "settlement_status", "mode_of_service", "logistics_status",
		 "pickup_scheduled_at", "picked_up_at", "out_for_delivery_at", "delivered_back_at"],
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
			"gogizmo_share", "customer_share", "mode_of_service", "logistics_status",
		],
		order_by="creation desc",
	)
