"""Supplier Scheme Circular — master document defining a supplier/brand incentive scheme."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, date_diff, flt, getdate, nowdate

_APPROVER_ROLES = ("Purchase Manager", "Scheme Manager", "System Manager")


def _is_approver():
	"""Return True if the current user holds an approval role."""
	return any(frappe.db.exists("Has Role", {"parent": frappe.session.user, "role": r}) for r in _APPROVER_ROLES)


class SupplierSchemeCircular(Document):
	def validate(self):
		self._validate_dates()
		self._validate_rules()
		self._compute_days_remaining()
		self._compute_totals()

	def before_submit(self):
		# Allow programmatic submit from TPD offer approve() to bypass approver check
		if self.flags.get("tpd_auto_create"):
			return
		if not _is_approver():
			frappe.throw(
				_("Only a Purchase Manager or Scheme Manager can activate a Supplier Scheme Circular. "
				  "Please use 'Submit for Review' and ask a manager to approve it."),
				title=_("Approval Required"),
			)

	def on_submit(self):
		self.db_set("status", "Active")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def on_update_after_submit(self):
		self._compute_totals()

	def _validate_dates(self):
		if getdate(self.valid_from) > getdate(self.valid_to):
			frappe.throw(_("Valid From cannot be after Valid To"), title=_("Supplier Scheme Circular Error"))
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
			frappe.throw(_("At least one rule is required"), title=_("Supplier Scheme Circular Error"))
		for rule in self.rules:
			if not rule.rule_name:
				frappe.throw(_("Row {0}: Rule Name is required").format(rule.idx), title=_("Supplier Scheme Circular Error"))
			# Fetch details from DB (nested child tables not loaded on parent rows)
			details = frappe.get_all(
				"Scheme Rule Detail",
				filters={"parent": rule.name, "parenttype": "Supplier Scheme Rule"},
				fields=["idx", "payout_per_unit", "additional_payout", "qty_from", "qty_to"],
			) if rule.name else []
			for detail in details:
				if flt(detail.payout_per_unit) < 0:
					frappe.throw(_("Row {0}: Payout per unit cannot be negative").format(detail.idx), title=_("Supplier Scheme Circular Error"))
				if flt(detail.additional_payout) < 0:
					frappe.throw(_("Row {0}: Additional payout cannot be negative").format(detail.idx), title=_("Supplier Scheme Circular Error"))
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
	def recompute_achievements(self) -> None:
		"""Trigger full recomputation of eligibility and payouts for this scheme."""
		frappe.has_permission("Supplier Scheme Circular", "write", throw=True)
		from ch_item_master.supplier_scheme.engine import recompute_scheme
		result = recompute_scheme(self.name)
		self.reload()
		return result

	@frappe.whitelist()
	def generate_claim(self) -> None:
		"""Generate a Scheme Claim Summary from current achievement data."""
		frappe.has_permission("Scheme Claim Summary", "create", throw=True)
		from ch_item_master.supplier_scheme.claim_engine import generate_claim_summary
		return generate_claim_summary(self.name)

	@frappe.whitelist()
	def link_existing_maps(self, names_json) -> dict:
		"""Bulk-link unlinked Scheme Product Map records to this scheme.

		Args:
			names_json: JSON array of Scheme Product Map names to link.
		Returns:
			dict with 'linked' count.
		"""
		import json
		frappe.has_permission("Supplier Scheme Circular", "write", throw=True)
		names = json.loads(names_json) if isinstance(names_json, str) else names_json
		linked = 0
		for name in names:
			if frappe.db.exists("Scheme Product Map", name):
				frappe.db.set_value("Scheme Product Map", name, "scheme", self.name, update_modified=False)
				linked += 1
		return {"linked": linked}

	@frappe.whitelist()
	def submit_for_review(self) -> None:
		"""Maker step: move a Draft scheme into Pending Approval for manager sign-off."""
		if self.docstatus != 0:
			frappe.throw(_("Only saved (Draft) schemes can be submitted for review."))
		if self.status not in ("Draft",):
			frappe.throw(_("Scheme is already '{0}'. Only Draft schemes can be submitted for review.").format(self.status))
		self.db_set("status", "Pending Approval")
		# Notify approvers
		approver_emails = frappe.db.sql("""
			SELECT DISTINCT u.email
			FROM `tabUser` u
			JOIN `tabHas Role` hr ON hr.parent = u.name
			WHERE hr.role IN ('Purchase Manager','Scheme Manager','System Manager')
			  AND u.enabled = 1 AND u.email != ''
		""", as_list=True)
		if approver_emails:
			recipients = [row[0] for row in approver_emails]
			scheme_url = frappe.utils.get_url_to_form("Supplier Scheme Circular", self.name)
			frappe.sendmail(
				recipients=recipients,
				subject=_("Scheme Approval Required: {0}").format(self.scheme_name or self.name),
				message=_(
					"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
					"<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>Congruence Holdings - Scheme Approval</div>"
					"<div style='padding:16px'>"
					"<p>Scheme <b>{name}</b> - <b>{scheme}</b> ({brand}) has been submitted for your approval.</p>"
					"<p>Please review and approve or reject the scheme.</p>"
					"<p><a href='{scheme_url}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Scheme</a></p>"
					"</div></div>"
				).format(name=self.name, scheme=self.scheme_name or "", brand=self.brand or "", scheme_url=scheme_url),
				delayed=False,
			)
		frappe.msgprint(_("Scheme submitted for review. Approvers have been notified."), indicator="blue", alert=True)

	@frappe.whitelist()
	def approve_scheme(self) -> None:
		"""Checker step: approve and activate the scheme (submits the document)."""
		if not _is_approver():
			frappe.throw(_("Only a Purchase Manager or Scheme Manager can approve schemes."), title=_("Not Authorised"))
		if self.docstatus != 0 or self.status != "Pending Approval":
			frappe.throw(_("Only schemes in 'Pending Approval' state can be approved."))
		self.db_set("reviewed_by", frappe.session.user)
		self.db_set("review_date", nowdate())
		# submit() will trigger on_submit → status = Active
		self.reload()
		self.submit()
		frappe.msgprint(_("Scheme approved and is now Active."), indicator="green", alert=True)

	@frappe.whitelist()
	def reject_scheme(self, reason: str = "") -> None:
		"""Checker step: reject the scheme and return it to Draft with a note."""
		if not _is_approver():
			frappe.throw(_("Only a Purchase Manager or Scheme Manager can reject schemes."), title=_("Not Authorised"))
		if self.docstatus != 0 or self.status != "Pending Approval":
			frappe.throw(_("Only schemes in 'Pending Approval' state can be rejected."))
		self.db_set("status", "Draft")
		self.db_set("reviewed_by", frappe.session.user)
		self.db_set("review_date", nowdate())
		self.db_set("review_notes", reason or "")
		frappe.msgprint(_("Scheme rejected and returned to Draft."), indicator="orange", alert=True)

	@frappe.whitelist()
	def close_scheme(self) -> None:
		"""Close this scheme (no further achievement entries)."""
		if self.docstatus != 1:
			frappe.throw(_("Only submitted schemes can be closed"), title=_("Supplier Scheme Circular Error"))
		self.db_set("status", "Closed")
		self.reload()
