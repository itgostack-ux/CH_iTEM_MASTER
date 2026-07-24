# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, nowdate, getdate

from ch_item_master.security import require_scoped_document_action


_PRICE_BATCH_SUBMIT_ROLES = ("CH Price Manager", "CH Master Manager")
_PRICE_BATCH_REVISE_ROLES = ("CH Price Manager", "CH Master Manager")
_PRICE_BATCH_OVERRIDE_ROLES = ("CH Master Manager",)
_PRICE_BATCH_APPLY_ROLES = ("CH Category Head", "CH Price Manager", "CH Master Manager")


def _safe_float(val):
	"""Convert a value to float, stripping commas and whitespace (e.g. '60,000' → 60000.0)."""
	if not val:
		return 0.0
	if isinstance(val, (int, float)):
		return float(val)
	return float(str(val).replace(",", "").strip() or 0)


class CHPriceUploadBatch(Document):
	"""Maker / checker price-upload batch, routed per product category.

	On submit the batch splits into one approval row per CH Category, each
	routed to that category's manager (or the company head as fallback). An
	approver decides only their own category's rows; approved categories are
	applied straight away rather than waiting on the slowest approver.

	Lifecycle:
	  Draft → Pending Approval → Partially Approved → Approved
	                                                → Applying
	                                                → Applied / Partially Applied
	                          → Rejected

	``Legacy Import`` quarantines the batches side-loaded by raw SQL on
	2026-07-07; their prices are already live, so they must never re-apply.

		See ``ch_item_master.ch_item_master.price_approval`` for the routing rules.
	"""
	_APPROVAL_CONTEXT = object()
	_PROTECTED_FIELDS = (
		"status",
		"submitted_by",
		"submitted_at",
		"approved_by",
		"approved_at",
		"rejected_by",
		"rejected_at",
		"rejection_reason",
		"applied_count",
		"skipped_count",
		"error_count",
		"applied_at",
	)
	_ITEM_GOVERNANCE_FIELDS = (
		"item_code",
		"channel",
		"category",
		"change_type",
		"field_label",
		"old_value",
		"new_value",
		"reason",
		"approval_status",
		"status",
		"error_message",
	)
	_CATEGORY_GOVERNANCE_FIELDS = (
		"category",
		"approver",
		"routed_via",
		"status",
		"row_count",
		"total_value",
		"responded_at",
		"comments",
	)

	def _authorize_approval_transition(self):
		self.flags.ch_price_batch_approval_context = self._APPROVAL_CONTEXT

	def _has_approval_context(self):
		return self.flags.get("ch_price_batch_approval_context") is self._APPROVAL_CONTEXT

	@staticmethod
	def _rows_signature(rows, fields):
		return tuple(
			tuple(row.get(fieldname) for fieldname in fields)
			for row in (rows or [])
		)

	def _validate_approval_transition(self):
		if self._has_approval_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			if self.status not in (None, "", "Draft") or any(
				self.get(fieldname) not in (
					(None, "", 0) if fieldname in ("applied_count", "skipped_count", "error_count") else (None, "")
				)
				for fieldname in self._PROTECTED_FIELDS
				if fieldname != "status"
			):
				frappe.throw(
					_("Batch approval state is set only by the approval workflow."),
					frappe.PermissionError,
				)
			if self.category_approvals or any(
				row.approval_status not in (None, "", "Pending")
				or row.status not in (None, "", "Pending")
				or row.error_message
				for row in (self.items or [])
			):
				frappe.throw(
					_("Batch row decisions and outcomes are server-managed."),
					frappe.PermissionError,
				)
			return

		if any(self.get(fieldname) != before.get(fieldname) for fieldname in self._PROTECTED_FIELDS):
			frappe.throw(
				_("Batch approval state can only be changed through its workflow actions."),
				frappe.PermissionError,
			)
		if self._rows_signature(
			self.category_approvals, self._CATEGORY_GOVERNANCE_FIELDS
		) != self._rows_signature(
			before.category_approvals, self._CATEGORY_GOVERNANCE_FIELDS
		):
			frappe.throw(
				_("Category routing and decisions are server-managed."),
				frappe.PermissionError,
			)

		if before.status == "Draft":
			for row in self.items or []:
				if (
					row.approval_status not in (None, "", "Pending")
					or row.status not in (None, "", "Pending")
					or row.error_message
				):
					frappe.throw(
						_("Batch row decisions and outcomes are server-managed."),
						frappe.PermissionError,
					)
		elif self._rows_signature(
			self.items, self._ITEM_GOVERNANCE_FIELDS
		) != self._rows_signature(before.items, self._ITEM_GOVERNANCE_FIELDS):
			frappe.throw(
				_("Submitted batch rows are immutable. Revise the batch before editing."),
				frappe.PermissionError,
			)

	def _require_action(self, role_field, default_roles, action) -> None:
		require_scoped_document_action(
			self,
			role_field,
			default_roles,
			action=action,
			permission_types=("write",),
			lock=True,
		)

	@frappe.whitelist()
	def get_ui_capabilities(self) -> dict:
		"""Return category actions resolved from the server approval policy."""
		from ch_item_master.config import has_role_setting
		from ch_item_master.ch_item_master.price_approval import _is_override

		self.check_permission("read")
		pending_state = self.status in ("Pending Approval", "Partially Approved")
		user = frappe.session.user
		sod_allowed = self.submitted_by != user or has_role_setting(
			"break_glass_supervisor_roles", ("System Manager",), user=user
		)
		can_override = bool(pending_state and sod_allowed and _is_override(user))
		can_decide = bool(
			pending_state
			and sod_allowed
			and (
				has_role_setting(
					"price_batch_approval_roles",
					("CH Category Head", "CH Price Manager", "CH Master Manager"),
					user=user,
				)
				or can_override
			)
		)
		actionable = [
			row.category or ""
			for row in (self.category_approvals or [])
			if can_decide and row.status == "Pending" and row.approver == user
		]
		return {
			"actionable_categories": actionable,
			"can_override": can_override,
		}

	def validate(self):
		self._validate_approval_transition()
		if not self.items:
			frappe.throw(_("No changes to review — the items table is empty."), title=_("Ch Price Upload Batch Error"))
		self._update_summary()

	def _update_summary(self):
		selling = sum(1 for r in self.items if r.change_type == "Selling Price")
		buyback = sum(1 for r in self.items if r.change_type == "Buyback Price")
		tags = sum(1 for r in self.items if r.change_type == "Tag")
		self.total_changes = len(self.items)
		self.selling_price_changes = selling
		self.buyback_price_changes = buyback
		self.tag_changes = tags

	def _validate_price_sanity(self):
		"""Pre-flight validation before submission — catches errors early.

		For Selling Price changes: simulate the final state (existing values +
		this batch's changes) and check MRP >= MOP >= Selling Price hierarchy.
		For Buyback Price changes: ensure no negative values.
		"""
		from collections import defaultdict

		# Group selling price rows by (item_code, channel)
		selling_groups = defaultdict(dict)
		for row in self.items:
			if row.change_type == "Selling Price":
				key = (row.item_code, row.channel)
				field_map = {"MRP": "mrp", "MOP": "mop", "Selling Price": "selling_price"}
				field = field_map.get(row.field_label)
				if field:
					selling_groups[key][field] = _safe_float(row.new_value)
			elif row.change_type == "Buyback Price":
				new_val = _safe_float(row.new_value)
				if new_val < 0:
					frappe.throw(
						_("Row {0}: {1} cannot be negative ({2})").format(
							row.idx, row.field_label, new_val
						),
						title=_("Invalid Buyback Price"),
					)

		errors = []
		for (item_code, channel), new_fields in selling_groups.items():
			# Fetch the current values so we can simulate the merged state
			existing = frappe.db.get_value(
				"CH Item Price",
				{"item_code": item_code, "channel": channel,
				 "status": ("in", ["Active", "Scheduled"])},
				["mrp", "mop", "selling_price"],
				as_dict=True,
			) or {}

			mrp = new_fields.get("mrp", _safe_float(existing.get("mrp")))
			mop = new_fields.get("mop", _safe_float(existing.get("mop")))
			sp  = new_fields.get("selling_price", _safe_float(existing.get("selling_price")))

			item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code

			if sp <= 0:
				errors.append(_("{0} ({1}): Selling Price must be > 0").format(item_name, channel))
			if mrp and mop and mrp < mop:
				errors.append(
					_("{0} ({1}): MRP ({2}) cannot be less than MOP ({3})").format(
						item_name, channel, mrp, mop
					)
				)
			if mop and sp and mop < sp:
				errors.append(
					_("{0} ({1}): MOP ({2}) cannot be less than Selling Price ({3})").format(
						item_name, channel, mop, sp
					)
				)
			if mrp and sp and mrp < sp:
				errors.append(
					_("{0} ({1}): MRP ({2}) cannot be less than Selling Price ({3})").format(
						item_name, channel, mrp, sp
					)
				)

		if errors:
			msg = _("Price hierarchy errors found — please fix before submitting:") + "<br><br>"
			msg += "<br>".join(errors)
			frappe.throw(msg, title=_("Invalid Price Hierarchy"))

	# ── Workflow actions (called from JS buttons) ─────────────────────────

	@frappe.whitelist(methods=["POST"])
	def submit_for_approval(self) -> None:
		"""Maker submits the batch; it is split and routed per category.

		Each distinct CH Category in the batch becomes its own approval row
		owned by that category's manager (or the company head when no manager
		is mapped), so an approver only ever decides their own rows.
		"""
		self._require_action(
			"price_batch_submit_roles",
			_PRICE_BATCH_SUBMIT_ROLES,
			_("submit a price upload batch for approval"),
		)
		self._authorize_approval_transition()
		from ch_item_master.ch_item_master.price_approval import (
			build_category_approvals,
			notify_approvers,
		)

		if self.status != "Draft":
			frappe.throw(_("Only Draft batches can be submitted for approval."), title=_("Ch Price Upload Batch Error"))
		if not self.company:
			frappe.throw(
				_("Company is required — approvals are routed per company."),
				title=_("Company Required"),
			)
		self._validate_price_sanity()

		unrouted = build_category_approvals(self)
		if unrouted:
			frappe.throw(
				_(
					"No approver could be resolved for: {0}.<br><br>Set a "
					"<b>Category Manager</b> on those CH Category records, or a "
					"<b>Company Head</b> on {1} as a fallback."
				).format(", ".join(frappe.bold(c) for c in unrouted), frappe.bold(self.company)),
				title=_("Cannot Route Approval"),
			)

		self.status = "Pending Approval"
		self.submitted_by = frappe.session.user
		self.submitted_at = now_datetime()
		for row in self.items:
			row.approval_status = "Pending"
		self.save()

		notify_approvers(self)

		count = len(self.category_approvals)
		frappe.msgprint(
			_("Submitted to {0} category approver(s).").format(count),
			indicator="blue",
		)

	@frappe.whitelist(methods=["POST"])
	def revise_batch(self) -> None:
		"""Allow maker to revise a rejected or partially-applied batch.

		Resets status to Draft, clears per-row statuses, and lets the user
		edit rows (fix prices, remove bad rows) before resubmitting.
		"""
		self._require_action(
			"price_batch_revise_roles",
			_PRICE_BATCH_REVISE_ROLES,
			_("revise a price upload batch"),
		)
		self._authorize_approval_transition()
		if self.status not in ("Rejected", "Partially Applied", "Applying"):
			frappe.throw(
				_("Only Rejected, Partially Applied or stuck Applying batches can be revised."),
				title=_("Ch Price Upload Batch Error"),
			)

		# Reset row-level statuses — keep Applied rows as-is, reset others
		for row in self.items:
			if row.status in ("Error", "Skipped", "Pending"):
				row.status = "Pending"
				row.error_message = ""
			row.approval_status = "Pending"

		# Clear the previous approval round: routing is recomputed on resubmit,
		# and leaving the old approver stamped showed a phantom checker on a
		# batch nobody had yet approved.
		self.set("category_approvals", [])
		self.approved_by = None
		self.approved_at = None
		self.rejected_by = None
		self.rejected_at = None
		self.rejection_reason = None

		self.status = "Draft"
		self.save()
		frappe.msgprint(_("Batch reset to Draft — you can now edit and resubmit."), indicator="blue")

	@frappe.whitelist(methods=["POST"])
	def approve_and_apply(self) -> None:
		"""Override path: approve every still-pending category at once.

		Reserved for override roles. The normal route is per-category via
		``price_approval.decide_category``; this exists so a System Manager can
		unblock a batch whose category owner is unavailable.
		"""
		self._require_action(
			"price_batch_override_roles",
			_PRICE_BATCH_OVERRIDE_ROLES,
			_("approve and apply an entire price upload batch"),
		)
		self._authorize_approval_transition()
		from ch_item_master.ch_item_master.price_approval import _is_override
		from ch_item_master.ch_item_master.rbac import check_sod

		if self.status not in ("Pending Approval", "Partially Approved"):
			frappe.throw(_("Only batches awaiting approval can be approved."), title=_("Ch Price Upload Batch Error"))
		if not _is_override():
			frappe.throw(
				_(
					"Approving a whole batch requires an override role. Approve "
					"your own categories individually instead."
				),
				frappe.PermissionError,
				title=_("Not Permitted"),
			)
		check_sod(self.submitted_by, frappe.session.user)

		stamp = now_datetime()
		for row in self.category_approvals:
			if row.status == "Pending":
				row.status = "Approved"
				row.responded_at = stamp
				row.comments = _("Approved in bulk by override role.")
		for row in self.items:
			row.approval_status = "Approved"

		self.approved_by = frappe.session.user
		self.approved_at = stamp
		self.status = "Approved"
		self.save()

		self.apply_approved_categories()

	@frappe.whitelist(methods=["POST"])
	def reject_batch(self, reason=None) -> None:
		"""Override path: reject every still-pending category at once."""
		self._require_action(
			"price_batch_override_roles",
			_PRICE_BATCH_OVERRIDE_ROLES,
			_("reject an entire price upload batch"),
		)
		self._authorize_approval_transition()
		from ch_item_master.ch_item_master.price_approval import _is_override

		if self.status not in ("Pending Approval", "Partially Approved"):
			frappe.throw(_("Only batches awaiting approval can be rejected."), title=_("Ch Price Upload Batch Error"))
		if not _is_override():
			frappe.throw(
				_(
					"Rejecting a whole batch requires an override role. Reject "
					"your own categories individually instead."
				),
				frappe.PermissionError,
				title=_("Not Permitted"),
			)
		if not (reason or "").strip():
			frappe.throw(_("A reason is required to reject."), title=_("Reason Required"))

		stamp = now_datetime()
		for row in self.category_approvals:
			if row.status == "Pending":
				row.status = "Rejected"
				row.responded_at = stamp
				row.comments = reason
		for row in self.items:
			if row.approval_status != "Approved":
				row.approval_status = "Rejected"

		# Categories already approved and applied stay applied; only the
		# undecided remainder is rejected here.
		self.status = "Rejected" if not any(
			r.status == "Approved" for r in self.category_approvals
		) else "Partially Applied"
		self.rejected_by = frappe.session.user
		self.rejected_at = now_datetime()
		self.rejection_reason = reason
		self.save()
		frappe.msgprint(_("Batch rejected."), indicator="red")

	@frappe.whitelist(methods=["POST"])
	def apply_approved_categories(self) -> dict:
		"""Apply only the rows whose category has been approved.

		Called after each category decision so approved pricing goes live
		without waiting on the slowest approver (SAP partial release). Rows
		belonging to pending or rejected categories are left untouched.
		"""
		self._require_action(
			"price_batch_apply_roles",
			_PRICE_BATCH_APPLY_ROLES,
			_("apply approved price upload categories"),
		)
		self._authorize_approval_transition()
		if self.status not in ("Approved", "Partially Approved", "Partially Applied", "Applying"):
			frappe.throw(_("This batch has no approved categories ready to apply."))
		approved_categories = {
			row.category or ""
			for row in (self.category_approvals or [])
			if row.status == "Approved"
		}
		pending_rows = [
			r for r in self.items
			if r.approval_status == "Approved"
			and (r.category or "") in approved_categories
			and r.status in ("Pending", "Error")
		]
		if not pending_rows:
			return {"applied": 0, "skipped": 0, "errors": 0}

		self.status = "Applying"
		self.save()

		try:
			result = self._apply_changes(rows=pending_rows)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Price Upload Batch Apply Error")
			frappe.throw(
				_("Error while applying changes. Check Error Log for details."),
				title=_("Ch Price Upload Batch Error"),
			)
		return result

	# ── Apply logic ──────────────────────────────────────────────────────

	def _apply_changes(self, rows=None):
		"""Walk the given child rows and apply them to the target DocType.

		``rows`` defaults to every row; the per-category flow passes only the
		rows whose category has been approved.
		"""
		applied = 0
		skipped = 0
		errors = 0
		target_rows = self.items if rows is None else rows

		# Group rows by (item_code, channel, change_type) for batch upserts
		from collections import defaultdict
		groups = defaultdict(list)
		for row in target_rows:
			groups[(row.item_code, row.channel or "", row.change_type)].append(row)

		for (item_code, channel, change_type), rows in groups.items():
			try:
				if change_type == "Selling Price":
					a, s = self._apply_selling_price(item_code, channel, rows)
					applied += a
					skipped += s
				elif change_type == "Buyback Price":
					a, s = self._apply_buyback_price(item_code, rows)
					applied += a
					skipped += s
				elif change_type == "Tag":
					a, s = self._apply_tags(item_code, rows)
					applied += a
					skipped += s
			except Exception as e:
				errors += len(rows)
				for row in rows:
					row.status = "Error"
					row.error_message = str(e)[:200]

		# Accumulate — a batch is applied in several passes as each category
		# is approved, so counters must not reset on every pass.
		self.applied_count = (self.applied_count or 0) + applied
		self.skipped_count = (self.skipped_count or 0) + skipped
		self.error_count = (self.error_count or 0) + errors
		self.applied_at = now_datetime()

		still_pending = any(r.status == "Pending" for r in (self.category_approvals or []))
		if still_pending:
			# Other categories have yet to decide — the batch is not finished.
			self.status = "Partially Approved"
		elif (self.error_count or 0) > 0 or any(
			r.status == "Rejected" for r in (self.category_approvals or [])
		):
			self.status = "Partially Applied"
		else:
			self.status = "Applied"

		# ── Write price change logs for applied rows ──────────────────────
		self._write_change_logs(rows=target_rows)

		self.save()
		return {"applied": applied, "skipped": skipped, "errors": errors}

	def _apply_selling_price(self, item_code, channel, rows):
		"""Apply selling price changes to CH Item Price."""
		applied = 0
		skipped = 0

		# Find existing active price. Company is part of the key: without it a
		# second company's batch would overwrite the first company's price
		# instead of creating its own.
		price_filters = {
			"item_code": item_code,
			"channel": channel,
			"status": ("in", ["Active", "Scheduled"]),
		}
		if self.company:
			price_filters["company"] = self.company
		existing_name = frappe.db.get_value("CH Item Price", price_filters, "name")

		if existing_name:
			doc = frappe.get_doc("CH Item Price", existing_name)
			changed = False
			for row in rows:
				field_map = {
					"MRP": "mrp", "MOP": "mop", "Selling Price": "selling_price",
				}
				field = field_map.get(row.field_label)
				if not field:
					row.status = "Skipped"
					row.error_message = f"Unknown field '{row.field_label}'"
					skipped += 1
					continue
				new_val = _safe_float(row.new_value)
				cur_val = _safe_float(doc.get(field))
				if cur_val != new_val:
					doc.set(field, new_val)
					changed = True
					row.status = "Applied"
					applied += 1
				else:
					row.status = "Skipped"
					row.error_message = f"No change — current {row.field_label} is already {cur_val}"
					skipped += 1

			if changed:
				doc._authorize_approval_transition()
				doc.flags.from_price_batch = True
				doc.save(ignore_permissions=True)
		else:
			# Create new CH Item Price
			doc = frappe.new_doc("CH Item Price")
			doc.item_code = item_code
			doc.channel = channel
			doc.effective_from = nowdate()
			doc.status = "Active"
			if self.company:
				doc.company = self.company

			for row in rows:
				field_map = {
					"MRP": "mrp", "MOP": "mop", "Selling Price": "selling_price",
				}
				field = field_map.get(row.field_label)
				if field:
					doc.set(field, _safe_float(row.new_value))
					row.status = "Applied"
					applied += 1
				else:
					row.status = "Skipped"
					row.error_message = f"Unknown field '{row.field_label}'"
					skipped += 1

			if not doc.selling_price:
				# selling_price is required — skip if not set
				for row in rows:
					row.status = "Skipped"
					row.error_message = "Selling Price is required but not set"
				return 0, len(rows)

			doc.flags.from_price_batch = True
			doc._authorize_approval_transition()
			doc.insert(ignore_permissions=True)

		return applied, skipped

	def _apply_buyback_price(self, item_code, rows):
		"""Apply buyback price changes to Buyback Price Master."""
		applied = 0
		skipped = 0

		existing_name = frappe.db.get_value(
			"Buyback Price Master",
			{"item_code": item_code, "is_active": 1},
			"name",
		)

		if existing_name:
			doc = frappe.get_doc("Buyback Price Master", existing_name)
		else:
			doc = frappe.new_doc("Buyback Price Master")
			doc.item_code = item_code
			doc.is_active = 1

		changed = False
		for row in rows:
			field = row.channel  # channel field stores the actual DB field name for buyback
			if not field or not hasattr(doc, field):
				row.status = "Skipped"
				row.error_message = f"Unknown buyback field '{field or '(empty)'}'"
				skipped += 1
				continue
			new_val = _safe_float(row.new_value)
			cur_val = _safe_float(doc.get(field))
			if cur_val != new_val:
				doc.set(field, new_val)
				changed = True
				row.status = "Applied"
				applied += 1
			else:
				row.status = "Skipped"
				row.error_message = f"No change — current {row.field_label} is already {cur_val}"
				skipped += 1

		if changed:
			doc.flags.from_price_batch = True
			if existing_name:
				doc.save(ignore_permissions=True)
			else:
				doc.insert(ignore_permissions=True)

		return applied, skipped

	def _apply_tags(self, item_code, rows):
		"""Apply tag changes — add or remove CH Item Commercial Tags."""
		applied = 0
		skipped = 0

		for row in rows:
			tag_name = (row.new_value or "").strip()
			old_tag = (row.old_value or "").strip()

			try:
				if tag_name and not old_tag:
					# Add tag
					existing = frappe.db.exists(
						"CH Item Commercial Tag",
						{"item_code": item_code, "tag": tag_name, "status": "Active"},
					)
					if existing:
						row.status = "Skipped"
						row.error_message = f"Tag '{tag_name}' already active"
						skipped += 1
					else:
						doc = frappe.new_doc("CH Item Commercial Tag")
						doc.item_code = item_code
						doc.tag = tag_name
						doc.status = "Active"
						doc.effective_from = nowdate()
						if self.company:
							doc.company = self.company
						doc.insert(ignore_permissions=True)
						row.status = "Applied"
						applied += 1

				elif old_tag and not tag_name:
					# Remove tag — set status to Expired
					existing = frappe.db.get_value(
						"CH Item Commercial Tag",
						{"item_code": item_code, "tag": old_tag, "status": "Active"},
						"name",
					)
					if existing:
						frappe.db.set_value(
							"CH Item Commercial Tag", existing,
							{"status": "Expired", "effective_to": nowdate()},
						)
						row.status = "Applied"
						applied += 1
					else:
						row.status = "Skipped"
						row.error_message = f"Tag '{old_tag}' not found as active — nothing to remove"
						skipped += 1

				elif old_tag and tag_name and old_tag != tag_name:
					# Replace tag — expire old, add new
					old_existing = frappe.db.get_value(
						"CH Item Commercial Tag",
						{"item_code": item_code, "tag": old_tag, "status": "Active"},
						"name",
					)
					if old_existing:
						frappe.db.set_value(
							"CH Item Commercial Tag", old_existing,
							{"status": "Expired", "effective_to": nowdate()},
						)

					doc = frappe.new_doc("CH Item Commercial Tag")
					doc.item_code = item_code
					doc.tag = tag_name
					doc.status = "Active"
					doc.effective_from = nowdate()
					if self.company:
						doc.company = self.company
					doc.insert(ignore_permissions=True)
					row.status = "Applied"
					applied += 1
				else:
					row.status = "Skipped"
					row.error_message = "No tag change detected"
					skipped += 1
			except Exception as e:
				row.status = "Error"
				row.error_message = str(e)[:200]

		return applied, skipped

	# ── Change log persistence ────────────────────────────────────────────

	def _write_change_logs(self, rows=None):
		"""Create CH Price Change Log entries for the rows applied in this pass.

		``rows`` is scoped to the pass so that a batch applied over several
		category approvals does not re-log rows applied in an earlier pass.
		"""
		# Field label → DB field name mapping for selling prices
		_selling_field_map = {"MRP": "mrp", "MOP": "mop", "Selling Price": "selling_price"}

		batch_reason = ""
		if (self.notes or "").strip():
			for line in (self.notes or "").splitlines():
				line = (line or "").strip()
				if line.lower().startswith("reason:"):
					batch_reason = line.split(":", 1)[1].strip()
					break
			if not batch_reason:
				batch_reason = (self.notes or "").strip()

		for row in (self.items if rows is None else rows):
			if row.status != "Applied":
				continue

			field_name = ""
			if row.change_type == "Selling Price":
				field_name = _selling_field_map.get(row.field_label, row.field_label)
			elif row.change_type == "Buyback Price":
				field_name = row.channel  # stores the DB field name
			elif row.change_type == "Tag":
				field_name = "tag"

			log = frappe.new_doc("CH Price Change Log")
			log.item_code = row.item_code
			log.channel = row.channel if row.change_type == "Selling Price" else ""
			log.change_type = row.change_type
			log.field_name = field_name
			log.field_label = row.field_label
			log.old_value = row.old_value
			log.new_value = row.new_value
			log.source = "Upload Batch"
			log.batch_ref = self.name
			log.reason = (row.reason or "").strip() or batch_reason
			log.changed_by = self.approved_by or frappe.session.user
			log.changed_at = now_datetime()
			log.insert(ignore_permissions=True)
