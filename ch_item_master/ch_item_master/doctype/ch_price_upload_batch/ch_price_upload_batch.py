# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, nowdate, getdate


class CHPriceUploadBatch(Document):
	"""Maker / Checker price-upload batch.

	Lifecycle:
	  Draft → Pending Approval → Approved → Applying → Applied / Partially Applied
	                            → Rejected
	"""

	def validate(self):
		if not self.items:
			frappe.throw(_("No changes to review — the items table is empty."))
		self._update_summary()

	def _update_summary(self):
		selling = sum(1 for r in self.items if r.change_type == "Selling Price")
		buyback = sum(1 for r in self.items if r.change_type == "Buyback Price")
		tags = sum(1 for r in self.items if r.change_type == "Tag")
		self.total_changes = len(self.items)
		self.selling_price_changes = selling
		self.buyback_price_changes = buyback
		self.tag_changes = tags

	# ── Workflow actions (called from JS buttons) ─────────────────────────

	@frappe.whitelist()
	def submit_for_approval(self):
		"""Maker submits the batch for checker review."""
		if self.status != "Draft":
			frappe.throw(_("Only Draft batches can be submitted for approval."))
		self.status = "Pending Approval"
		self.save(ignore_permissions=True)
		frappe.msgprint(_("Batch submitted for approval."), indicator="blue")

	@frappe.whitelist()
	def approve_and_apply(self):
		"""Checker approves — immediately applies all pending changes."""
		if self.status != "Pending Approval":
			frappe.throw(_("Only 'Pending Approval' batches can be approved."))

		self.status = "Applying"
		self.approved_by = frappe.session.user
		self.approved_at = now_datetime()
		self.save(ignore_permissions=True)

		try:
			self._apply_changes()
		except Exception:
			frappe.db.commit()
			frappe.log_error(frappe.get_traceback(), "Price Upload Batch Apply Error")
			frappe.throw(_("Error while applying changes. Check Error Log for details."))

	@frappe.whitelist()
	def reject_batch(self, reason=None):
		"""Checker rejects the batch."""
		if self.status != "Pending Approval":
			frappe.throw(_("Only 'Pending Approval' batches can be rejected."))
		self.status = "Rejected"
		self.rejected_by = frappe.session.user
		self.rejected_at = now_datetime()
		if reason:
			self.rejection_reason = reason
		self.save(ignore_permissions=True)
		frappe.msgprint(_("Batch rejected."), indicator="red")

	# ── Apply logic ──────────────────────────────────────────────────────

	def _apply_changes(self):
		"""Walk each child row and apply to the target DocType."""
		applied = 0
		skipped = 0
		errors = 0

		# Group rows by (item_code, channel, change_type) for batch upserts
		from collections import defaultdict
		groups = defaultdict(list)
		for row in self.items:
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

		self.applied_count = applied
		self.skipped_count = skipped
		self.error_count = errors
		self.applied_at = now_datetime()

		if errors == 0:
			self.status = "Applied"
		elif applied > 0:
			self.status = "Partially Applied"
		else:
			self.status = "Partially Applied"

		# ── Write price change logs for applied rows ──────────────────────
		self._write_change_logs()

		self.save(ignore_permissions=True)
		frappe.db.commit()

	def _apply_selling_price(self, item_code, channel, rows):
		"""Apply selling price changes to CH Item Price."""
		applied = 0
		skipped = 0

		# Find existing active price
		existing_name = frappe.db.get_value(
			"CH Item Price",
			{"item_code": item_code, "channel": channel,
			 "status": ("in", ["Active", "Scheduled"])},
			"name",
		)

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
				new_val = float(row.new_value or 0)
				cur_val = float(doc.get(field) or 0)
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
					doc.set(field, float(row.new_value or 0))
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
			new_val = float(row.new_value or 0)
			cur_val = float(doc.get(field) or 0)
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

	def _write_change_logs(self):
		"""Create CH Price Change Log entries for every Applied row."""
		# Field label → DB field name mapping for selling prices
		_selling_field_map = {"MRP": "mrp", "MOP": "mop", "Selling Price": "selling_price"}

		for row in self.items:
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
			log.reason = row.reason or ""
			log.changed_by = self.approved_by or frappe.session.user
			log.changed_at = now_datetime()
			log.insert(ignore_permissions=True)
