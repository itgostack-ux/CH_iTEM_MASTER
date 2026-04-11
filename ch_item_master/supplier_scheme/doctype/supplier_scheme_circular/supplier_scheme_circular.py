"""Supplier Scheme Circular — master document defining a supplier/brand incentive scheme."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, date_diff, flt, getdate, nowdate


class SupplierSchemeCircular(Document):
	def validate(self):
		self._validate_dates()
		self._validate_rules()
		self._compute_days_remaining()
		self._compute_totals()

	def on_submit(self):
		self.db_set("status", "Active")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def on_update_after_submit(self):
		self._compute_totals()

	def _validate_dates(self):
		if getdate(self.valid_from) > getdate(self.valid_to):
			frappe.throw(_("Valid From cannot be after Valid To"))
		# Warn on overlapping schemes for same brand
		overlaps = frappe.db.sql("""
			SELECT name, scheme_name
			FROM `tabSupplier Scheme Circular`
			WHERE brand = %s
			  AND name != %s
			  AND docstatus = 1
			  AND valid_from <= %s
			  AND valid_to >= %s
		""", (self.brand, self.name, self.valid_to, self.valid_from), as_dict=True)
		if overlaps:
			names = ", ".join(o.name for o in overlaps)
			frappe.msgprint(
				_("Warning: Overlapping scheme period with {0}").format(names),
				indicator="orange",
			)

	def _validate_rules(self):
		if not self.rules:
			frappe.throw(_("At least one rule is required"))
		for rule in self.rules:
			if not rule.rule_name:
				frappe.throw(_("Row {0}: Rule Name is required").format(rule.idx))
			# Fetch details from DB (nested child tables not loaded on parent rows)
			details = frappe.get_all(
				"Scheme Rule Detail",
				filters={"parent": rule.name, "parenttype": "Supplier Scheme Rule"},
				fields=["idx", "payout_per_unit", "additional_payout", "qty_from", "qty_to"],
			) if rule.name else []
			for detail in details:
				if flt(detail.payout_per_unit) < 0:
					frappe.throw(_("Row {0}: Payout per unit cannot be negative").format(detail.idx))
				if flt(detail.additional_payout) < 0:
					frappe.throw(_("Row {0}: Additional payout cannot be negative").format(detail.idx))
				if cint(detail.qty_from) > cint(detail.qty_to) and cint(detail.qty_to) > 0:
					frappe.throw(
						_("Row {0}: Qty From ({1}) > Qty To ({2})").format(
							detail.idx, detail.qty_from, detail.qty_to
						)
					)

	def _compute_days_remaining(self):
		today = getdate(nowdate())
		end = getdate(self.valid_to)
		if today > end:
			self.days_remaining = 0
		else:
			self.days_remaining = date_diff(end, today)

	def _compute_totals(self):
		"""Aggregate achievement data from ledger into scheme totals."""
		result = frappe.db.sql("""
			SELECT
				SUM(CASE WHEN is_reversed = 0 AND eligible_for_slab = 1 THEN qty ELSE 0 END) as eligible_qty,
				SUM(CASE WHEN is_reversed = 0 THEN computed_payout ELSE 0 END) as total_payout
			FROM `tabScheme Achievement Ledger`
			WHERE scheme = %s AND is_reversed = 0
		""", self.name, as_dict=True)
		row = result[0] if result else {}
		self.total_eligible_qty = flt(row.get("eligible_qty"))
		self.total_claim_amount = flt(row.get("total_payout"))

		settled = frappe.db.sql("""
			SELECT IFNULL(SUM(received_amount), 0) as settled
			FROM `tabScheme Settlement`
			WHERE scheme = %s AND docstatus = 1
		""", self.name)
		self.total_settled_amount = flt(settled[0][0]) if settled else 0
		self.total_pending_amount = flt(self.total_claim_amount) - flt(self.total_settled_amount)

	@frappe.whitelist()
	def recompute_achievements(self):
		"""Trigger full recomputation of eligibility and payouts for this scheme."""
		frappe.has_permission("Supplier Scheme Circular", "write", throw=True)
		from ch_item_master.supplier_scheme.engine import recompute_scheme
		result = recompute_scheme(self.name)
		self.reload()
		return result

	@frappe.whitelist()
	def generate_claim(self):
		"""Generate a Scheme Claim Summary from current achievement data."""
		frappe.has_permission("Scheme Claim Summary", "create", throw=True)
		from ch_item_master.supplier_scheme.claim_engine import generate_claim_summary
		return generate_claim_summary(self.name)

	@frappe.whitelist()
	def close_scheme(self):
		"""Close this scheme (no further achievement entries)."""
		if self.docstatus != 1:
			frappe.throw(_("Only submitted schemes can be closed"))
		self.db_set("status", "Closed")
		self.reload()
