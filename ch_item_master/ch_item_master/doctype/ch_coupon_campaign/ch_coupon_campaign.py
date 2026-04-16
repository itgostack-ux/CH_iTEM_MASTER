# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import secrets
import string

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, nowdate, now_datetime


class CHCouponCampaign(Document):

	# ── Lifecycle ────────────────────────────────────────────────────────────

	def validate(self):
		self._validate_dates()
		self._validate_coupon_settings()
		self._validate_voucher_settings()

	def on_submit(self):
		self.db_set("status", "Active")

	def before_submit(self):
		self._generate_codes()

	def on_cancel(self):
		self._cancel_generated_codes()
		self.db_set("status", "Cancelled")

	# ── Validation ───────────────────────────────────────────────────────────

	def _validate_dates(self):
		if getdate(self.valid_upto) < getdate(self.valid_from):
			frappe.throw(_("Valid Upto must be on or after Valid From"))

	def _validate_coupon_settings(self):
		if self.campaign_type not in ("Coupon Code", "Both"):
			return
		if flt(self.discount_value) <= 0:
			frappe.throw(_("Discount value must be positive"))
		if self.discount_type == "Discount Percentage" and flt(self.discount_value) > 100:
			frappe.throw(_("Discount percentage cannot exceed 100%"))
		if cint(self.coupon_quantity) <= 0:
			frappe.throw(_("Coupon quantity must be at least 1"))
		if cint(self.coupon_quantity) > 10000:
			frappe.throw(_("Coupon quantity cannot exceed 10,000 per campaign"))

	def _validate_voucher_settings(self):
		if self.campaign_type not in ("Voucher", "Both"):
			return
		if flt(self.face_value) <= 0:
			frappe.throw(_("Voucher face value must be positive"))
		if flt(self.face_value) > 500000:
			frappe.throw(_("Voucher face value cannot exceed ₹5,00,000"))
		if cint(self.voucher_quantity) <= 0:
			frappe.throw(_("Voucher quantity must be at least 1"))
		if cint(self.voucher_quantity) > 10000:
			frappe.throw(_("Voucher quantity cannot exceed 10,000 per campaign"))

	# ── Code Generation ──────────────────────────────────────────────────────

	def _generate_codes(self):
		if self.campaign_type in ("Coupon Code", "Both"):
			self._generate_coupon_codes()
		if self.campaign_type in ("Voucher", "Both"):
			self._generate_voucher_codes()
		self._update_stats_fields()

	def _generate_coupon_codes(self):
		pr = self._create_pricing_rule()
		self.db_set("pricing_rule", pr.name)

		if self.code_generation == "Single Shared Code":
			code = self._make_code(self.code_prefix)
			cc = frappe.get_doc({
				"doctype": "Coupon Code",
				"coupon_name": self.campaign_name,
				"coupon_code": code,
				"coupon_type": "Promotional",
				"pricing_rule": pr.name,
				"valid_from": self.valid_from,
				"valid_upto": self.valid_upto,
				"maximum_use": cint(self.coupon_quantity),
			})
			cc.insert(ignore_permissions=True)
			self.append("codes", {
				"code": cc.coupon_code,
				"instrument_type": "Coupon",
				"reference_doctype": "Coupon Code",
				"reference_name": cc.name,
				"status": "Generated",
			})
		else:
			# Unique codes — one Coupon Code record per code
			qty = cint(self.coupon_quantity)
			max_use = cint(self.max_use_per_code) or 1
			used_codes = set()

			for i in range(qty):
				code = self._make_unique_code(self.code_prefix, used_codes)
				used_codes.add(code)

				cc = frappe.get_doc({
					"doctype": "Coupon Code",
					"coupon_name": f"{self.campaign_name} #{i + 1:04d}",
					"coupon_code": code,
					"coupon_type": "Promotional",
					"pricing_rule": pr.name,
					"valid_from": self.valid_from,
					"valid_upto": self.valid_upto,
					"maximum_use": max_use,
				})
				cc.insert(ignore_permissions=True)
				self.append("codes", {
					"code": cc.coupon_code,
					"instrument_type": "Coupon",
					"reference_doctype": "Coupon Code",
					"reference_name": cc.name,
					"status": "Generated",
				})

	def _generate_voucher_codes(self):
		qty = cint(self.voucher_quantity)

		for _i in range(qty):
			voucher = frappe.get_doc({
				"doctype": "CH Voucher",
				"voucher_type": self.voucher_type or "Promo Voucher",
				"company": self.company,
				"original_amount": flt(self.face_value),
				"valid_from": str(self.valid_from),
				"valid_upto": str(self.valid_upto),
				"source_type": "Promotion",
				"reason": f"Campaign: {self.campaign_name}",
				"single_use": cint(self.single_use_voucher),
				"min_order_amount": flt(self.min_order_amount),
				"max_discount_amount": flt(self.max_voucher_discount),
				"applicable_item_group": self.applicable_item_group,
			})
			voucher.insert(ignore_permissions=True)

			# Add Issue transaction and submit (activates the voucher)
			voucher.append("transactions", {
				"transaction_type": "Issue",
				"amount": flt(self.face_value),
				"balance_after": flt(self.face_value),
				"transaction_date": now_datetime(),
				"reference_doctype": "CH Coupon Campaign",
				"reference_document": self.name,
				"note": f"Issued via campaign: {self.campaign_name}",
			})
			voucher.save(ignore_permissions=True)
			voucher.submit()

			self.append("codes", {
				"code": voucher.voucher_code,
				"instrument_type": "Voucher",
				"reference_doctype": "CH Voucher",
				"reference_name": voucher.name,
				"status": "Generated",
			})

	# ── Pricing Rule Creation ────────────────────────────────────────────────

	def _create_pricing_rule(self):
		apply_on = "Transaction" if self.apply_on == "Grand Total" else self.apply_on

		pr = frappe.get_doc({
			"doctype": "Pricing Rule",
			"title": f"Campaign: {self.campaign_name}",
			"apply_on": apply_on,
			"price_or_product_discount": "Price",
			"rate_or_discount": self.discount_type,
			"coupon_code_based": 1,
			"selling": 1,
			"buying": 0,
			"company": self.company,
			"valid_from": self.valid_from,
			"valid_upto": self.valid_upto,
			"disable": 0,
		})

		if self.discount_type == "Discount Percentage":
			pr.discount_percentage = flt(self.discount_value)
		else:
			pr.discount_amount = flt(self.discount_value)

		if flt(self.min_order_amount) > 0:
			pr.min_amt = flt(self.min_order_amount)

		# Item-level restrictions
		if apply_on == "Item Group" and self.apply_on_item_group:
			pr.append("item_groups", {"item_group": self.apply_on_item_group})
		elif apply_on == "Brand" and self.apply_on_brand:
			pr.append("brands", {"brand": self.apply_on_brand})

		pr.insert(ignore_permissions=True)
		return pr

	# ── Code Generation Helpers ──────────────────────────────────────────────

	def _make_code(self, prefix=None):
		"""Generate a single random code with optional prefix."""
		alpha = string.ascii_uppercase
		alnum = string.ascii_uppercase + string.digits
		# First 2 chars alpha (avoids autoname collision), rest alphanumeric
		suffix = (
			secrets.choice(alpha)
			+ secrets.choice(alpha)
			+ "".join(secrets.choice(alnum) for _ in range(4))
		)
		return f"{prefix}-{suffix}" if prefix else suffix

	def _make_unique_code(self, prefix, used_codes, max_attempts=200):
		"""Generate a unique code not in used_codes and not in existing Coupon Codes."""
		for _attempt in range(max_attempts):
			code = self._make_code(prefix)
			if code in used_codes:
				continue
			if frappe.db.exists("Coupon Code", {"coupon_code": code}):
				continue
			return code
		frappe.throw(_("Could not generate unique code after {0} attempts").format(max_attempts))

	# ── Cancel ───────────────────────────────────────────────────────────────

	def _cancel_generated_codes(self):
		for row in self.codes:
			try:
				if row.reference_doctype == "Coupon Code":
					# Disable the coupon by setting max_use to 0
					frappe.db.set_value(
						"Coupon Code", row.reference_name, "maximum_use", 0,
						update_modified=False,
					)
				elif row.reference_doctype == "CH Voucher":
					doc = frappe.get_doc("CH Voucher", row.reference_name)
					if doc.docstatus == 1 and doc.status in ("Active", "Partially Used"):
						doc.cancel()
			except Exception:
				frappe.log_error(
					title=f"Campaign cancel: failed to cancel {row.reference_name}",
				)

		# Disable the pricing rule
		if self.pricing_rule:
			frappe.db.set_value("Pricing Rule", self.pricing_rule, "disable", 1,
								update_modified=False)

	# ── Stats Refresh ────────────────────────────────────────────────────────

	@frappe.whitelist()
	def refresh_stats(self):
		"""Recalculate performance metrics from live Coupon Code / CH Voucher data."""
		self._refresh_stats_from_db()
		self.flags.ignore_validate_update_after_submit = True
		self.save(ignore_permissions=True)

	def _refresh_stats_from_db(self):
		total_gen = len(self.codes)
		redeemed = 0
		distributed = 0
		total_discount = 0
		total_revenue = 0

		for row in self.codes:
			if row.status == "Distributed":
				distributed += 1

			if row.instrument_type == "Coupon":
				used = cint(frappe.db.get_value("Coupon Code", row.reference_name, "used"))
				if used > 0 and row.status not in ("Redeemed",):
					row.status = "Redeemed"
					# Get revenue from invoices using this coupon
					inv_data = frappe.db.sql("""
						SELECT COALESCE(SUM(grand_total), 0) as revenue,
							   COALESCE(SUM(discount_amount), 0) as discount
						FROM `tabPOS Invoice`
						WHERE coupon_code = %s AND docstatus = 1
					""", row.reference_name, as_dict=True)
					si_data = frappe.db.sql("""
						SELECT COALESCE(SUM(grand_total), 0) as revenue,
							   COALESCE(SUM(discount_amount), 0) as discount
						FROM `tabSales Invoice`
						WHERE coupon_code = %s AND docstatus = 1
					""", row.reference_name, as_dict=True)
					row.redeemed_amount = flt(inv_data[0].discount) + flt(si_data[0].discount)
					total_revenue += flt(inv_data[0].revenue) + flt(si_data[0].revenue)
					total_discount += flt(row.redeemed_amount)

				if used > 0:
					redeemed += 1

			elif row.instrument_type == "Voucher":
				v = frappe.db.get_value("CH Voucher", row.reference_name,
					["status", "original_amount", "balance"], as_dict=True)
				if v:
					used_amt = flt(v.original_amount) - flt(v.balance)
					if v.status == "Fully Used":
						row.status = "Redeemed"
						redeemed += 1
					elif v.status == "Partially Used":
						row.status = "Partially Redeemed"
						redeemed += 1
					row.redeemed_amount = used_amt
					total_discount += used_amt

		self.total_codes_generated = total_gen
		self.total_distributed = distributed
		self.total_redeemed = redeemed
		self.total_discount_given = total_discount
		self.total_revenue_generated = total_revenue
		self.redemption_rate = (redeemed / total_gen * 100) if total_gen else 0

	def _update_stats_fields(self):
		"""Quick stats update after generation (no redemption data yet)."""
		self.total_codes_generated = len(self.codes)
		self.total_distributed = 0
		self.total_redeemed = 0
		self.total_discount_given = 0
		self.total_revenue_generated = 0
		self.redemption_rate = 0

	# ── Export ────────────────────────────────────────────────────────────────

	@frappe.whitelist()
	def export_codes(self):
		"""Return list of codes for CSV export."""
		rows = []
		for row in self.codes:
			rows.append({
				"code": row.code,
				"type": row.instrument_type,
				"status": row.status,
				"assigned_to": row.assigned_to or "",
				"assigned_to_name": row.assigned_to_name or "",
				"distributed_via": row.distributed_via or "",
			})
		return rows

	# ── Bulk Distribution ────────────────────────────────────────────────────

	@frappe.whitelist()
	def mark_distributed(self, channel="Manual"):
		"""Mark all Generated codes as Distributed."""
		now = now_datetime()
		updated = 0
		for row in self.codes:
			if row.status == "Generated":
				row.status = "Distributed"
				row.distributed_via = channel
				row.distributed_on = now
				updated += 1
		if updated:
			self.total_distributed = sum(1 for r in self.codes if r.status == "Distributed")
			self.flags.ignore_validate_update_after_submit = True
			self.save(ignore_permissions=True)
		return {"updated": updated}


# ── Scheduled: Auto-expire campaigns ────────────────────────────────────────

def expire_campaigns():
	"""Daily scheduled task: expire campaigns past valid_upto."""
	today = nowdate()
	campaigns = frappe.get_all(
		"CH Coupon Campaign",
		filters={"status": ["in", ["Active", "Paused"]], "valid_upto": ["<", today], "docstatus": 1},
		pluck="name",
	)
	for name in campaigns:
		frappe.db.set_value("CH Coupon Campaign", name, "status", "Expired",
							update_modified=True)
	if campaigns:
		frappe.db.commit()
