# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import json
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, flt, getdate, now_datetime

from ch_item_master.ch_item_master.utils import validate_indian_phone
from ch_item_master.config import get_int_setting, get_list_setting, has_role_setting
from ch_item_master.security import require_scoped_document_action


_DELIVERY_NOTE_CREATION_ROLES = ("Sales User", "Sales Manager")


class CHExceptionRequest(Document):
	_APPROVAL_CONTEXT = object()
	_PROTECTED_FIELDS = (
		"status",
		"assigned_approver",
		"approval_role",
		"approval_channel",
		"approver",
		"approver_name",
		"approved_at",
		"resolved_at",
		"resolved_by",
		"approval_expiry",
		"otp_reference",
		"resolution_value",
		"last_escalated_at",
	)

	def _authorize_approval_transition(self):
		self.flags.ch_exception_approval_context = self._APPROVAL_CONTEXT

	def _has_approval_context(self):
		return self.flags.get("ch_exception_approval_context") is self._APPROVAL_CONTEXT

	def _validate_approval_transition(self):
		if self._has_approval_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			frappe.throw(
				_("Create exception requests through the scoped exception API."),
				frappe.PermissionError,
			)
		if any(self.get(fieldname) != before.get(fieldname) for fieldname in self._PROTECTED_FIELDS):
			frappe.throw(
				_("Exception routing, decisions, and approval evidence can only be changed through authorized actions."),
				frappe.PermissionError,
			)

	@frappe.whitelist()
	def get_ui_capabilities(self):
		"""Return server-authoritative actions available to the current user."""
		self.check_permission("read")
		can_review = bool(
			self.docstatus == 0
			and self.status in ("Pending", "Escalated")
			and frappe.has_permission(self.doctype, "write", doc=self)
		)
		if can_review:
			try:
				self._validate_approver(None)
				from ch_item_master.ch_item_master.rbac import check_sod

				check_sod(submitted_by=self.requested_by, approver=frappe.session.user)
			except (frappe.PermissionError, frappe.ValidationError):
				can_review = False

		return {
			"can_review": can_review,
			"requires_otp": bool(
				can_review
				and frappe.get_cached_value(
					"CH Exception Type", self.exception_type, "requires_otp"
				)
			),
		}

	def before_insert(self):
		self.raised_at = now_datetime()
		self.requested_by = frappe.session.user
		request = getattr(frappe.local, "request", None)
		self.ip_address = request.remote_addr if request else ""

		if self.requested_by:
			self.requested_by_name = frappe.db.get_value(
				"User", self.requested_by, "full_name"
			) or ""

		if self.item_code and not self.item_name:
			self.item_name = frappe.db.get_value("Item", self.item_code, "item_name") or ""

		if self.customer and not self.customer_name:
			self.customer_name = frappe.db.get_value(
				"Customer", self.customer, "customer_name"
			) or ""

		if self.item_code and not self.purchase_price:
			self.purchase_price = flt(frappe.db.get_value(
				"Item", self.item_code, "valuation_rate"
			))
			if not self.purchase_price and self.store_warehouse:
				self.purchase_price = flt(frappe.db.get_value(
					"Bin",
					{"item_code": self.item_code, "warehouse": self.store_warehouse},
					"valuation_rate",
				))

	def validate(self):
		self._validate_approval_transition()
		if self.customer_phone:
			self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
		self._validate_exception_type()
		self._check_daily_limit()
		self._validate_customer_for_pos()

	def _validate_customer_for_pos(self):
		"""POS-raised exceptions must carry a real customer identity.

		Doctrine (Oracle Xstore / SAP CAR-POS / Dynamics 365 Commerce):
		manager approvals are customer-scoped grants — they cannot be
		floating credits reusable across bills. Defense-in-depth for the
		exception_api.raise_exception guard.
		"""
		if not self.pos_profile:
			return
		if not self.customer:
			frappe.throw(
				_("Customer is mandatory for POS exception requests. "
				  "Select a customer on the cart before raising an exception."),
				title=_("Customer Required"),
			)
		walk_in = frappe.db.get_value("POS Profile", self.pos_profile, "customer")
		if walk_in and self.customer == walk_in:
			frappe.throw(
				_("Cannot raise an exception for the walk-in customer. "
				  "Register the customer first, then raise the exception."),
				title=_("Real Customer Required"),
			)

	def _validate_approver(self, approver):
		authenticated_user = frappe.session.user
		if approver and approver != authenticated_user:
			frappe.throw(
				_("Approver identity is derived from the authenticated session."),
				frappe.PermissionError,
			)
		approver = authenticated_user

		if self.assigned_approver:
			if approver == self.assigned_approver or has_role_setting(
				"exception_override_roles", ("System Manager",), user=approver
			):
				return approver
			frappe.throw(
				_("This exception is assigned to {0}. Only that user can approve or reject it.").format(
					self.assigned_approver
				),
				title=_("Unauthorized Approver"),
			)

		if not has_role_setting(
			"exception_approval_roles",
			("Store Manager", "Sales Manager", "Service Manager", "System Manager"),
			user=approver,
		):
			frappe.throw(
				_("User {0} is not authorized to approve or reject exception requests.").format(approver),
				title=_("Unauthorized Approver"),
			)
		return approver

	def _resolve_audit_event(self):
		etype = (self.exception_type or "").lower()
		if any(k in etype for k in ("discount", "price", "msp", "margin")):
			return "Discount Override"
		if "return" in etype:
			return "Return Approved"
		if any(k in etype for k in ("buyback", "exchange")):
			return "Buyback Value Edit"
		if any(k in etype for k in ("repair", "estimate", "service")):
			return "Repair Estimate Revision"
		return "Other"

	def _write_audit_log(self, before=None, after=None, remarks=None, event_type=None, user=None):
		try:
			from ch_pos.audit import log_business_event

			log_business_event(
				event_type=event_type or self._resolve_audit_event(),
				ref_doctype=self.doctype,
				ref_name=self.name,
				before=before,
				after=after,
				remarks=remarks or self.resolution_remarks or "",
				store=self.store_warehouse,
				company=self.company,
				user=user or self.approver or self.requested_by,
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"CH Exception Request audit log failed for {self.name}"
			)

	def before_submit(self):
		if self.status == "Pending":
			frappe.throw(
				_("Cannot submit a Pending exception. Approve or Reject first.")
			)

	def _validate_exception_type(self):
		etype = frappe.get_cached_doc("CH Exception Type", self.exception_type)
		if not etype.enabled:
			frappe.throw(
				_("Exception type {0} is disabled").format(self.exception_type)
			)

		company_name = self.company
		ggr_match = company_name in get_list_setting("exception_ggr_companies")
		gfs_match = company_name in get_list_setting("exception_gfs_companies")

		if ggr_match and not etype.applicable_to_ggr:
			frappe.throw(
				_("Exception type {0} is not applicable to {1}").format(
					self.exception_type, company_name
				)
			)
		if gfs_match and not etype.applicable_to_gfs:
			frappe.throw(
				_("Exception type {0} is not applicable to {1}").format(
					self.exception_type, company_name
				)
			)

	def _check_daily_limit(self):
		if not self.store_warehouse:
			return

		etype = frappe.get_cached_doc("CH Exception Type", self.exception_type)
		max_per_day = etype.max_occurrences_per_day or 0
		if max_per_day <= 0:
			return

		today_start = getdate()
		count = frappe.db.count("CH Exception Request", {
			"exception_type": self.exception_type,
			"store_warehouse": self.store_warehouse,
			"raised_at": (">=", str(today_start)),
			"name": ("!=", self.name or ""),
			"docstatus": ("!=", 2),
		})

		if count >= max_per_day:
			frappe.throw(
				_("Daily limit of {0} for {1} at this store has been reached").format(
					max_per_day, self.exception_type
				)
			)

	def approve(self, approver=None, channel=None, otp_reference=None,
	            resolution_value=None, remarks=None):
		"""Approve this exception request."""
		approver = self._validate_approver(approver)
		from ch_item_master.ch_item_master.rbac import check_sod

		check_sod(submitted_by=self.requested_by, approver=approver)
		self._authorize_approval_transition()
		now = now_datetime()
		prior_status = self.status or "Pending"

		self.status = "Approved"
		self.approver = approver
		self.approver_name = frappe.db.get_value("User", approver, "full_name") or ""
		self.approval_channel = channel or "Manager PIN"
		self.approved_at = now
		self.resolved_at = now
		self.resolved_by = approver

		if otp_reference:
			self.otp_reference = otp_reference

		if resolution_value is not None:
			self.resolution_value = flt(resolution_value)
		else:
			self.resolution_value = flt(self.requested_value)

		if remarks:
			self.resolution_remarks = remarks

		etype = frappe.get_cached_doc("CH Exception Type", self.exception_type)
		validity_minutes = etype.validity_minutes or 30
		self.approval_expiry = add_to_date(now, minutes=validity_minutes)

		self.save(ignore_permissions=True)
		self.submit()

		self._write_audit_log(
			before=prior_status,
			after=f"Approved via {self.approval_channel}",
			remarks=self.resolution_remarks or "Exception approved",
			user=approver,
		)
		return self

	def reject(self, approver=None, reason=None):
		"""Reject this exception request."""
		approver = self._validate_approver(approver)
		from ch_item_master.ch_item_master.rbac import check_sod

		check_sod(submitted_by=self.requested_by, approver=approver)
		self._authorize_approval_transition()
		now = now_datetime()
		prior_status = self.status or "Pending"

		reason = (reason or self.resolution_remarks or "").strip()
		if not reason:
			frappe.throw(
				_("Rejection reason is mandatory for exception requests."),
				title=_("Reason Required"),
			)

		self.status = "Rejected"
		self.approver = approver
		self.approver_name = frappe.db.get_value("User", approver, "full_name") or ""
		self.resolved_at = now
		self.resolved_by = approver
		self.resolution_remarks = reason

		self.save(ignore_permissions=True)
		self.submit()
		self._write_audit_log(
			before=prior_status,
			after="Rejected",
			remarks=reason,
			event_type="Other",
			user=approver,
		)
		return self

	def is_valid(self):
		if self.status not in ("Approved", "Auto-Approved") or self.docstatus != 1:
			return False
		if self.approval_expiry and now_datetime() > self.approval_expiry:
			return False
		# Same-day rule (Oracle Xstore / SAP CAR-POS parity): an approval
		# consumed on a later day is a governance leak — the store manager
		# who approved a discount on Monday did NOT approve it for
		# Wednesday's bill. Reject the exception once its raise date has
		# rolled over, regardless of approval_expiry (some etypes carry
		# multi-day validity_minutes which is safe for back-office
		# workflows but not for POS bill consumption).
		if self.pos_profile and self.raised_at:
			if getdate(self.raised_at) != getdate():
				return False
		return True

	# ═══════════════════════════════════════════════════════════════════════
	# DELIVERY NOTE INTEGRATION — Auto-sync approved amount to DN item
	# ═══════════════════════════════════════════════════════════════════════

	def on_submit(self):
		"""After approval submission, apply to the reference document."""
		if self.status in ("Approved", "Auto-Approved"):
			self._apply_to_reference_document()

	def _apply_to_reference_document(self):
		"""Dispatch to the correct downstream handler."""
		if not self.reference_doctype or not self.reference_name:
			return
		if self.reference_doctype == "Delivery Note":
			self._apply_to_delivery_note()





	def _apply_to_delivery_note(self):
		"""Push approved exception to Delivery Note row + sync Sales Order Item.
		
		🔑 KEY: When DN is linked to a Sales Order, we must ALSO update the
		SO Item's rate/amount, otherwise ERPNext's overbill check fails when
		the DN is saved/submitted.
		"""
		dn_name = self.reference_name

		debug_log = [f"[EXR {self.name}] Applying to DN={dn_name}"]

		if not frappe.db.exists("Delivery Note", dn_name):
			frappe.log_error("\n".join(debug_log), "Exception Apply: DN not found")
			return

		dn_docstatus = frappe.db.get_value("Delivery Note", dn_name, "docstatus")
		if dn_docstatus == 1:
			frappe.throw(_("Cannot apply exception — DN {0} already submitted.").format(dn_name))
		if dn_docstatus == 2:
			frappe.throw(_("Cannot apply exception — DN {0} cancelled.").format(dn_name))

		approved_deduction = flt(self.resolution_value or self.requested_value)
		if approved_deduction <= 0:
			frappe.msgprint(
				_("Exception {0}: no approved amount to apply.").format(self.name),
				indicator="orange", alert=True,
			)
			return

		target_serial = (self.serial_no or "").strip()
		if not target_serial:
			frappe.msgprint(
				_("Exception {0}: no serial number.").format(self.name),
				indicator="orange", alert=True,
			)
			return

		# ── Locate DN row ──────────────────────────────────────────────────
		dn_items = frappe.db.sql(
			"""
			SELECT name, idx, item_code, serial_no, qty, rate,
				price_list_rate, warehouse,
				against_sales_order, so_detail
			FROM `tabDelivery Note Item`
			WHERE parent = %s
			ORDER BY idx
			""",
			(dn_name,), as_dict=True
		)

		target_row = None
		for it in dn_items:
			serials = [s.strip() for s in (it.serial_no or "").split("\n") if s.strip()]
			if target_serial in serials:
				target_row = it
				break

		if not target_row:
			frappe.msgprint(
				_("Exception {0}: serial {1} not found in DN {2}.").format(
					self.name, target_serial, dn_name
				),
				indicator="red", alert=True,
			)
			return

		# ── Compute new rate ───────────────────────────────────────────────
		qty = flt(target_row.qty) or 1
		original_rate = flt(target_row.rate)
		original_value = flt(original_rate * qty, 2)

		row_serials = [s.strip() for s in (target_row.serial_no or "").split("\n") if s.strip()]
		if not row_serials:
			row_serials = [target_serial]

		prior_deduction = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(COALESCE(resolution_value, requested_value)), 0)
			FROM `tabCH Exception Request`
			WHERE reference_doctype = 'Delivery Note'
			AND reference_name = %s
			AND status IN ('Approved', 'Auto-Approved')
			AND docstatus = 1
			AND name != %s
			AND serial_no IN %s
			""",
			(dn_name, self.name, tuple(row_serials)),
		)[0][0] or 0

		total_deduction = flt(flt(prior_deduction) + flt(approved_deduction), 2)

		if total_deduction > original_value:
			frappe.throw(
				_("Row #{0}: Total deductions ({1}) exceed row value ({2}).").format(
					target_row.idx, total_deduction, original_value
				)
			)

		new_item_amount = flt(original_value - total_deduction, 2)
		if new_item_amount < 0:
			new_item_amount = 0.0

		new_rate = flt(new_item_amount / qty, 2) if qty else 0

		debug_log.append(
			f"  qty={qty}, original_rate={original_rate}, new_rate={new_rate}, "
			f"new_amount={new_item_amount}"
		)

		# ── Update DN Item ─────────────────────────────────────────────────
		child_meta = frappe.get_meta("Delivery Note Item")

		base_updates = {
			"rate":                 new_rate,
			"base_rate":            new_rate,
			"net_rate":             new_rate,
			"base_net_rate":        new_rate,
			"price_list_rate":      new_rate,
			"base_price_list_rate": new_rate,
			"amount":               new_item_amount,
			"base_amount":          new_item_amount,
			"net_amount":           new_item_amount,
			"base_net_amount":      new_item_amount,
			"discount_amount":      0,
			"discount_percentage":  0,
		}

		custom_updates = {}
		if child_meta.has_field("custom_exception_type"):
			custom_updates["custom_exception_type"] = self.exception_type
		if child_meta.has_field("custom_exception_amount"):
			custom_updates["custom_exception_amount"] = total_deduction
		if child_meta.has_field("custom_exception_request"):
			custom_updates["custom_exception_request"] = self.name
		if child_meta.has_field("custom_original_rate"):
			custom_updates["custom_original_rate"] = original_rate

		all_updates = {**base_updates, **custom_updates}

		frappe.db.set_value(
			"Delivery Note Item",
			target_row.name,
			all_updates,
			update_modified=False,
		)
		debug_log.append(f"  ✅ Updated DN item {target_row.name}")

		# ═══════════════════════════════════════════════════════════════════
		# 🔑 SYNC SALES ORDER ITEM — prevents "Cannot overbill" error
		# ═══════════════════════════════════════════════════════════════════
		so_detail = target_row.get("so_detail")
		against_so = target_row.get("against_sales_order")

		if so_detail and against_so:
			try:
				self._sync_sales_order_item(against_so, so_detail, new_rate, new_item_amount, qty)
				debug_log.append(f"  ✅ Synced SO {against_so} item {so_detail}")
			except Exception as e:
				debug_log.append(f"  ⚠️ SO sync failed: {e}")
				frappe.log_error(frappe.get_traceback(), f"EXR {self.name}: SO Sync Failed")

		# ── Recalculate DN totals ──────────────────────────────────────────
		self._recalculate_dn_totals(dn_name)

		# ── DN header custom fields ────────────────────────────────────────
		dn_meta = frappe.get_meta("Delivery Note")
		if dn_meta.has_field("custom_exception_type"):
			frappe.db.set_value(
				"Delivery Note", dn_name,
				"custom_exception_type", self.exception_type,
				update_modified=False,
			)
		if dn_meta.has_field("custom_dn_status"):
			frappe.db.set_value(
				"Delivery Note", dn_name,
				"custom_dn_status", "Exception Approved",
				update_modified=False,
			)

		frappe.db.set_value(
			"Delivery Note", dn_name,
			"modified", now_datetime(),
			update_modified=False,
		)

		# ── Verify ─────────────────────────────────────────────────────────
		verify = frappe.db.get_value(
			"Delivery Note Item", target_row.name,
			["rate", "amount"], as_dict=True
		)
		debug_log.append(f"  🔍 VERIFY: rate={verify.rate}, amount={verify.amount}")

		if flt(verify.amount) != flt(new_item_amount):
			frappe.log_error("\n".join(debug_log), f"EXR {self.name}: Amount Mismatch")

		# ── Realtime push ──────────────────────────────────────────────────
		try:
			frappe.publish_realtime(
				event=f"dn_exception_applied:{dn_name}",
				message={
					"exception_name":  self.name,
					"exception_type":  self.exception_type,
					"original_value":  original_value,
					"original_rate":   original_rate,
					"new_rate":        new_rate,
					"deduction":       approved_deduction,
					"new_item_amount": new_item_amount,
					"row_idx":         target_row.idx,
					"row_name":        target_row.name,
					"dn":              dn_name,
					"reload":          True,
				},
				after_commit=True,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Realtime push failed")

		frappe.msgprint(
			_("✅ Exception <b>{0}</b> applied<br>"
			"Delivery Note: {1}<br>"
			"Row #{2}<br>"
			"Rate: ₹{3} → ₹{4}<br>"
			"Amount: ₹{5} → <b>₹{6}</b><br>"
			"DB verified: ₹{7}").format(
				self.name,
				frappe.utils.get_link_to_form("Delivery Note", dn_name),
				target_row.idx,
				original_rate,
				new_rate,
				original_value,
				new_item_amount,
				verify.amount,
			),
			indicator="green" if flt(verify.amount) == flt(new_item_amount) else "red",
			alert=True,
		)


	def _sync_sales_order_item(self, so_name, so_item_name, new_rate, new_amount, qty):
		"""Sync Sales Order Item rate/amount to match Delivery Note.
		
		This prevents ERPNext's overbill check from failing when the DN
		is saved/submitted after an exception changes its amount.
		
		Uses raw SQL because SO is typically submitted (docstatus=1) and
		normal .save() would fail validation.
		"""
		if not so_name or not so_item_name:
			return

		# Verify SO Item exists
		so_item = frappe.db.get_value(
			"Sales Order Item", so_item_name,
			["name", "parent", "rate", "amount"],
			as_dict=True
		)
		if not so_item:
			return

		so_docstatus = frappe.db.get_value("Sales Order", so_name, "docstatus")
		if so_docstatus == 2:
			return  # Cancelled — don't touch

		# Update SO Item rate + amount via raw SQL (bypasses validation on submitted docs)
		frappe.db.sql("""
			UPDATE `tabSales Order Item`
			SET rate            = %s,
				base_rate       = %s,
				net_rate        = %s,
				base_net_rate   = %s,
				price_list_rate = %s,
				base_price_list_rate = %s,
				amount          = %s,
				base_amount     = %s,
				net_amount      = %s,
				base_net_amount = %s,
				discount_amount = 0,
				discount_percentage = 0,
				modified        = %s
			WHERE name = %s
		""", (
			new_rate, new_rate, new_rate, new_rate, new_rate, new_rate,
			new_amount, new_amount, new_amount, new_amount,
			now_datetime(), so_item_name
		))

		# Recompute SO header totals
		so_items = frappe.db.sql("""
			SELECT amount, net_amount
			FROM `tabSales Order Item`
			WHERE parent = %s
		""", (so_name,), as_dict=True)

		so_total = flt(sum(flt(i.amount) for i in so_items), 2)
		so_net_total = flt(sum(flt(i.net_amount) for i in so_items), 2) or so_total

		so_tax = flt(frappe.db.get_value("Sales Order", so_name, "total_taxes_and_charges") or 0)
		so_grand = flt(so_total + so_tax, 2)
		so_rounded = flt(round(so_grand), 2)

		frappe.db.set_value("Sales Order", so_name, {
			"total":              so_total,
			"base_total":         so_total,
			"net_total":          so_net_total,
			"base_net_total":     so_net_total,
			"grand_total":        so_grand,
			"base_grand_total":   so_grand,
			"rounded_total":      so_rounded,
			"base_rounded_total": so_rounded,
		}, update_modified=False)


	def _recalculate_dn_totals(self, dn_name):
		"""Recompute Delivery Note header totals from item amounts."""
		items = frappe.db.sql(
			"""
			SELECT amount, net_amount
			FROM `tabDelivery Note Item`
			WHERE parent = %s
			""",
			(dn_name,), as_dict=True
		)
		total = flt(sum(flt(i.amount) for i in items), 2)
		net_total = flt(sum(flt(i.net_amount) for i in items), 2) or total

		tax_total = flt(frappe.db.get_value(
			"Delivery Note", dn_name, "total_taxes_and_charges"
		) or 0)

		grand_total = flt(total + tax_total, 2)
		rounded = flt(round(grand_total), 2)

		frappe.db.set_value(
			"Delivery Note", dn_name,
			{
				"total":              total,
				"base_total":         total,
				"net_total":          net_total,
				"base_net_total":     net_total,
				"grand_total":        grand_total,
				"base_grand_total":   grand_total,
				"rounded_total":      rounded,
				"base_rounded_total": rounded,
			},
			update_modified=False,
		)
# ═══════════════════════════════════════════════════════════════════════════════
# WHITELISTED API — called from Delivery Note JS
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["POST"])
def create_from_delivery_note(delivery_note, exception_type, items, requested_reason=None):
	"""Create ONE CH Exception Request per selected IMEI + set DN status.

	Field mapping per request:
	    reference_doctype  = "Delivery Note"
	    reference_name     = <DN name>
	    item_code          = <item from DN row>
	    serial_no          = <IMEI> (one per request)
	    requested_by       = session user
	    requested_reason   = <user-entered reason>
	    requested_value    = <exception/deduction amount for this IMEI>
	    original_value     = <server-side unit rate of the DN row>
	"""
	if isinstance(items, str):
		try:
			items = json.loads(items)
		except (TypeError, ValueError):
			frappe.throw(_("Items must be valid JSON."), frappe.ValidationError)

	if not isinstance(items, list) or not items:
		frappe.throw(_("At least one IMEI must be selected."))
	item_limit = get_int_setting("exception_delivery_note_item_limit", 50, minimum=1)
	if len(items) > item_limit:
		frappe.throw(
			_("A maximum of {0} IMEIs can be submitted at once.").format(item_limit),
			frappe.ValidationError,
		)
	if any(not isinstance(item, dict) for item in items):
		frappe.throw(_("Every item must be an object."), frappe.ValidationError)

	dn = frappe.get_doc("Delivery Note", delivery_note)
	require_scoped_document_action(
		dn,
		"exception_delivery_note_creation_roles",
		_DELIVERY_NOTE_CREATION_ROLES,
		action=_("create delivery-note exception requests"),
		permission_types=("read", "write"),
		store_field="set_warehouse",
		lock=True,
	)
	frappe.has_permission("CH Exception Request", "create", throw=True)

	exception_type = (exception_type or "").strip()
	etype = frappe.get_cached_doc("CH Exception Type", exception_type)
	if not etype.enabled:
		frappe.throw(_("Exception type {0} is disabled.").format(exception_type), frappe.ValidationError)

	reason = (requested_reason or "").strip()
	if not reason:
		frappe.throw(_("A reason is required."), frappe.ValidationError)
	reason_limit = get_int_setting("exception_request_reason_limit", 1000, minimum=1)
	if len(reason) > reason_limit:
		frappe.throw(
			_("Reason cannot exceed {0} characters.").format(reason_limit),
			frappe.ValidationError,
		)

	amount_limit = flt(get_int_setting("exception_request_amount_limit", 10_000_000, minimum=1))
	rows = {row.name: row for row in dn.items}
	validated = []
	seen = set()
	total_amount = 0.0
	warehouses = set()
	for item in items:
		row_name = (item.get("row_name") or "").strip()
		imei = (item.get("imei_no") or "").strip()
		amount = flt(item.get("exception_amount"))
		row = rows.get(row_name)
		if not row or not imei:
			frappe.throw(_("Every IMEI must reference an item row from this Delivery Note."), frappe.ValidationError)
		if (row_name, imei) in seen:
			frappe.throw(_("IMEI {0} was submitted more than once.").format(imei), frappe.ValidationError)
		seen.add((row_name, imei))

		if item.get("item_code") and item.get("item_code") != row.item_code:
			frappe.throw(_("Item code does not match Delivery Note row {0}.").format(row.idx), frappe.ValidationError)
		if item.get("item_name") and item.get("item_name") != row.item_name:
			frappe.throw(_("Item name does not match Delivery Note row {0}.").format(row.idx), frappe.ValidationError)

		row_serials = {
			serial.strip()
			for serial in re.split(r"[\n,]+", row.serial_no or "")
			if serial.strip()
		}
		if imei not in row_serials:
			frappe.throw(
				_("IMEI {0} does not belong to Delivery Note row {1}.").format(imei, row.idx),
				frappe.ValidationError,
			)

		unit_value = flt(row.rate, 2)
		if amount <= 0 or amount > unit_value or amount > amount_limit:
			frappe.throw(
				_("Exception amount for IMEI {0} must be positive and cannot exceed its item rate or the configured limit.").format(
					imei
				),
				frappe.ValidationError,
			)
		total_amount += amount
		if total_amount > amount_limit:
			frappe.throw(_("Total exception amount exceeds the configured limit."), frappe.ValidationError)

		warehouse = row.warehouse or dn.get("set_warehouse")
		if warehouse:
			warehouses.add(warehouse)
		validated.append((row, imei, amount, warehouse, unit_value))

	if warehouses:
		from ch_erp15.ch_erp15.scope import assert_user_has_store_scope

		for warehouse in warehouses:
			assert_user_has_store_scope(
				warehouse=warehouse,
				company=dn.company,
				msg=_("You are not permitted to create exceptions for this Delivery Note location."),
			)

	existing = frappe.get_all(
		"CH Exception Request",
		filters={
			"reference_doctype": "Delivery Note",
			"reference_name": dn.name,
			"exception_type": exception_type,
			"serial_no": ("in", [row[1] for row in validated]),
			"status": ("in", ["Pending", "Escalated", "Awaiting Approval"]),
			"docstatus": ("!=", 2),
		},
		fields=["name", "serial_no"],
		limit_page_length=item_limit,
	)
	if existing:
		frappe.throw(
			_("An open exception request already exists for IMEI {0}: {1}.").format(
				existing[0].serial_no, existing[0].name
			),
			frappe.ValidationError,
		)

	created = []
	for row, imei, amount, warehouse, unit_value in validated:
		doc = frappe.new_doc("CH Exception Request")
		doc.exception_type    = exception_type
		doc.company           = dn.company
		doc.store_warehouse   = warehouse
		doc.status            = "Pending"
		doc.reference_doctype = "Delivery Note"
		doc.reference_name    = delivery_note
		doc.item_code         = row.item_code
		doc.item_name         = row.item_name
		doc.serial_no         = imei
		doc.customer          = dn.customer
		doc.customer_name     = dn.customer_name
		doc.requested_by      = frappe.session.user
		doc.requested_reason  = reason
		doc.requested_value   = amount
		doc.original_value    = unit_value

		doc.insert()
		created.append(doc.name)

	# ── Set DN status to "Pending Approval" (if field exists) ────────────
	dn_meta = frappe.get_meta("Delivery Note")
	if dn_meta.has_field("custom_dn_status"):
		dn.custom_dn_status = "Pending Approval"
		dn.flags.ignore_validate_update_after_submit = True
		dn.save()
		frappe.publish_realtime(
			event=f"dn_status_changed:{delivery_note}",
			message={"status": "Pending Approval"},
			after_commit=True,
		)

	return {
		"count": len(created),
		"names": created,
		"first": created[0],
	}
