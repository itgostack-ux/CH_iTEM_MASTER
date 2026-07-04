# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, add_to_date, getdate, flt

from ch_item_master.ch_item_master.utils import validate_indian_phone


class CHExceptionRequest(Document):
	def before_insert(self):
		self.raised_at = now_datetime()
		self.requested_by = self.requested_by or frappe.session.user
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
		if self.customer_phone:
			self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
		self._validate_exception_type()
		self._check_daily_limit()

	def _validate_approver(self, approver):
		approver = approver or frappe.session.user
		roles = set(frappe.get_roles(approver))
		bypass_roles = {"System Manager", "Administrator"}

		if self.assigned_approver:
			if approver == self.assigned_approver or roles.intersection(bypass_roles):
				return approver
			frappe.throw(
				_("This exception is assigned to {0}. Only that user can approve or reject it.").format(
					self.assigned_approver
				),
				title=_("Unauthorized Approver"),
			)

		allowed_roles = {
			"Store Manager",
			"Sales Manager",
			"Service Manager",
			"System Manager",
			"Administrator",
		}
		if not roles.intersection(allowed_roles):
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
		ggr_match = "GoGizmo" in (company_name or "")
		gfs_match = "GoFix" in (company_name or "")

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

		frappe.db.commit()

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

@frappe.whitelist()
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
	    original_value     = <rate × qty of the DN row = current item.amount>
	"""
	import json
	if isinstance(items, str):
		items = json.loads(items)

	if not items:
		frappe.throw(_("At least one IMEI must be selected."))

	if not frappe.db.exists("Delivery Note", delivery_note):
		frappe.throw(_("Delivery Note {0} not found.").format(delivery_note))

	dn = frappe.get_doc("Delivery Note", delivery_note)

	created = []
	for it in items:
		imei = (it.get("imei_no") or "").strip()
		amount = flt(it.get("exception_amount"))
		row_name = it.get("row_name")

		if not imei or amount <= 0 or not row_name:
			continue

		# Locate the DN row to capture warehouse + original_value (= rate × qty)
		warehouse = None
		original_value = 0
		for row in dn.items:
			if row.name == row_name:
				warehouse = row.warehouse
				original_value = flt(row.rate) * flt(row.qty)
				break

		doc = frappe.new_doc("CH Exception Request")
		doc.exception_type    = exception_type
		doc.company           = dn.company
		doc.store_warehouse   = warehouse
		doc.status            = "Pending"
		doc.reference_doctype = "Delivery Note"
		doc.reference_name    = delivery_note
		doc.item_code         = it.get("item_code")
		doc.item_name         = it.get("item_name")
		doc.serial_no         = imei
		doc.customer          = dn.customer
		doc.customer_name     = dn.customer_name
		doc.requested_by      = frappe.session.user
		doc.requested_reason  = requested_reason
		doc.requested_value   = amount                       # the deduction amount
		doc.original_value    = flt(original_value, 2)       # rate × qty (current item.amount)

		doc.insert(ignore_permissions=True)
		created.append(doc.name)

	if not created:
		frappe.throw(_("No valid exception requests could be created."))

	# ── Set DN status to "Pending Approval" (if field exists) ────────────
	dn_meta = frappe.get_meta("Delivery Note")
	if dn_meta.has_field("custom_dn_status"):
		frappe.db.set_value(
			"Delivery Note", delivery_note,
			"custom_dn_status", "Pending Approval",
			update_modified=False,
		)
		try:
			frappe.publish_realtime(
				event=f"dn_status_changed:{delivery_note}",
				message={"status": "Pending Approval"},
				after_commit=True,
			)
		except Exception:
			pass

	frappe.db.commit()

	return {
		"count": len(created),
		"names": created,
		"first": created[0],
	}