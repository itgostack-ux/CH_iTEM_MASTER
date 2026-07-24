"""Supplier Scheme Circular — master document defining a supplier/brand incentive scheme."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, date_diff, flt, getdate, nowdate

from ch_item_master.config import get_enabled_role_emails, get_int_setting, has_role_setting
from ch_item_master.security import require_scoped_document_action


_SCHEME_APPROVAL_ROLES = ("Purchase Manager", "Scheme Manager")
_SCHEME_SUBMIT_ROLES = ("Accounts User", "Accounts Manager", "Purchase Manager", "Scheme Manager")
_SCHEME_MANAGEMENT_ROLES = ("Accounts Manager", "Purchase Manager", "Scheme Manager")


def _is_approver():
	"""Return True if the current user holds an approval role."""
	return has_role_setting(
		"supplier_scheme_approval_roles",
		("Purchase Manager", "Scheme Manager", "System Manager"),
	)


class SupplierSchemeCircular(Document):
	_APPROVAL_CONTEXT = object()
	_PROTECTED_FIELDS = ("status", "reviewed_by", "review_date", "review_notes")
	_REVIEW_SENSITIVE_FIELDS = (
		"scheme_name",
		"circular_number",
		"company",
		"brand",
		"supplier",
		"distributor",
		"source_upload",
		"source_upload_part",
		"issue_date",
		"valid_from",
		"valid_to",
		"settlement_type",
		"tds_applicable",
		"tds_percent",
		"description",
		"circular_attachment",
		"rules",
	)

	def _authorize_approval_transition(self):
		self.flags.supplier_scheme_approval_context = self._APPROVAL_CONTEXT

	def _has_approval_context(self):
		return self.flags.get("supplier_scheme_approval_context") is self._APPROVAL_CONTEXT

	def _validate_approval_transition(self):
		if self._has_approval_context():
			return
		before = self.get_doc_before_save() if not self.is_new() else None
		if before is None:
			if self.status not in (None, "", "Draft") or any(
				self.get(fieldname) not in (None, "")
				for fieldname in self._PROTECTED_FIELDS
				if fieldname != "status"
			):
				frappe.throw(_("Scheme approval evidence is server-managed."), frappe.PermissionError)
			return
		if any(self.get(fieldname) != before.get(fieldname) for fieldname in self._PROTECTED_FIELDS):
			frappe.throw(
				_("Scheme approval state can only be changed through its workflow actions."),
				frappe.PermissionError,
			)
		if before.status in ("Pending Approval", "Active") and any(
			self.get(fieldname) != before.get(fieldname)
			for fieldname in self._REVIEW_SENSITIVE_FIELDS
		):
			frappe.throw(
				_("Approved or pending scheme terms are immutable. Reject or amend the scheme first."),
				frappe.PermissionError,
			)

	def _require_action(self, role_field, default_roles, action, permission_types=("write",)) -> None:
		require_scoped_document_action(
			self,
			role_field,
			default_roles,
			action=action,
			permission_types=permission_types,
			company_field="company",
			lock=True,
		)

	@frappe.whitelist()
	def get_ui_capabilities(self) -> dict:
		"""Return review actions from the configured server approval policy."""
		self.check_permission("read")
		can_write = bool(frappe.has_permission(self.doctype, "write", doc=self, throw=False))
		return {
			"can_review": bool(
				can_write
				and self.docstatus == 0
				and self.status == "Pending Approval"
				and _is_approver()
			)
		}

	def validate(self):
		self._validate_approval_transition()
		self._validate_dates()
		self._validate_rules()
		self._compute_days_remaining()
		self._compute_totals()

	def before_submit(self):
		if not self._has_approval_context():
			frappe.throw(_("Use Approve Scheme to activate this scheme."), frappe.PermissionError)
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

	@frappe.whitelist(methods=["POST"])
	def recompute_achievements(self) -> None:
		"""Trigger full recomputation of eligibility and payouts for this scheme."""
		self._require_action(
			"supplier_scheme_management_roles",
			_SCHEME_MANAGEMENT_ROLES,
			_("recompute supplier scheme achievements"),
		)
		from ch_item_master.supplier_scheme.engine import recompute_scheme
		result = recompute_scheme(self.name)
		self.reload()
		return result

	@frappe.whitelist(methods=["POST"])
	def generate_claim(self) -> None:
		"""Generate a Scheme Claim Summary from current achievement data."""
		self._require_action(
			"supplier_scheme_management_roles",
			_SCHEME_MANAGEMENT_ROLES,
			_("generate a supplier scheme claim"),
		)
		frappe.has_permission("Scheme Claim Summary", "create", throw=True)
		from ch_item_master.supplier_scheme.claim_engine import generate_claim_summary
		return generate_claim_summary(self.name)

	@frappe.whitelist(methods=["POST"])
	def link_existing_maps(self, names_json) -> dict:
		"""Bulk-link unlinked Scheme Product Map records to this scheme.

		Args:
			names_json: JSON array of Scheme Product Map names to link.
		Returns:
			dict with 'linked' count.
		"""
		import json
		self._require_action(
			"supplier_scheme_management_roles",
			_SCHEME_MANAGEMENT_ROLES,
			_("link supplier scheme product maps"),
		)
		names = json.loads(names_json) if isinstance(names_json, str) else names_json
		if not isinstance(names, list):
			frappe.throw(_("Product map names must be a list."))
		limit = min(get_int_setting("supplier_scheme_link_limit", 200, minimum=1), 1000)
		names = list(dict.fromkeys(name for name in names if isinstance(name, str) and name))
		if len(names) > limit:
			frappe.throw(_("A maximum of {0} product maps can be linked at once.").format(limit))
		linked = 0
		for name in names:
			if frappe.db.exists("Scheme Product Map", name) and frappe.has_permission(
				"Scheme Product Map", "write", doc=name, user=frappe.session.user
			):
				frappe.db.set_value("Scheme Product Map", name, "scheme", self.name, update_modified=False)
				linked += 1
		return {"linked": linked}

	@frappe.whitelist(methods=["POST"])
	def submit_for_review(self) -> None:
		"""Maker step: move a Draft scheme into Pending Approval for manager sign-off."""
		self._require_action(
			"supplier_scheme_submit_roles",
			_SCHEME_SUBMIT_ROLES,
			_("submit a supplier scheme for review"),
		)
		if self.docstatus != 0:
			frappe.throw(_("Only saved (Draft) schemes can be submitted for review."))
		if self.status not in ("Draft",):
			frappe.throw(_("Scheme is already '{0}'. Only Draft schemes can be submitted for review.").format(self.status))
		self.status = "Pending Approval"
		self._authorize_approval_transition()
		self.save()
		# Notify approvers
		approver_emails = get_enabled_role_emails(
			_SCHEME_APPROVAL_ROLES,
			company=self.company,
		)
		if approver_emails:
			scheme_url = frappe.utils.get_url_to_form("Supplier Scheme Circular", self.name)
			try:
				frappe.sendmail(
					recipients=approver_emails,
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
					delayed=True,
				)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"Supplier Scheme approval notification failed: {self.name}",
				)
		frappe.msgprint(_("Scheme submitted for review. Approvers have been notified."), indicator="blue", alert=True)

	@frappe.whitelist(methods=["POST"])
	def approve_scheme(self) -> None:
		"""Checker step: approve and activate the scheme (submits the document)."""
		self._require_action(
			"supplier_scheme_approval_roles",
			_SCHEME_APPROVAL_ROLES,
			_("approve a supplier scheme"),
			permission_types=("write", "submit"),
		)
		if not _is_approver():
			frappe.throw(_("Only a Purchase Manager or Scheme Manager can approve schemes."), title=_("Not Authorised"))
		if self.docstatus != 0 or self.status != "Pending Approval":
			frappe.throw(_("Only schemes in 'Pending Approval' state can be approved."))
		self.reviewed_by = frappe.session.user
		self.review_date = nowdate()
		self._authorize_approval_transition()
		self.submit()
		frappe.msgprint(_("Scheme approved and is now Active."), indicator="green", alert=True)

	@frappe.whitelist(methods=["POST"])
	def reject_scheme(self, reason: str = "") -> None:
		"""Checker step: reject the scheme and return it to Draft with a note."""
		self._require_action(
			"supplier_scheme_approval_roles",
			_SCHEME_APPROVAL_ROLES,
			_("reject a supplier scheme"),
		)
		if not _is_approver():
			frappe.throw(_("Only a Purchase Manager or Scheme Manager can reject schemes."), title=_("Not Authorised"))
		if self.docstatus != 0 or self.status != "Pending Approval":
			frappe.throw(_("Only schemes in 'Pending Approval' state can be rejected."))
		self.status = "Draft"
		self.reviewed_by = frappe.session.user
		self.review_date = nowdate()
		self.review_notes = reason or ""
		self._authorize_approval_transition()
		self.save()
		frappe.msgprint(_("Scheme rejected and returned to Draft."), indicator="orange", alert=True)

	@frappe.whitelist(methods=["POST"])
	def close_scheme(self) -> None:
		"""Close this scheme (no further achievement entries)."""
		self._require_action(
			"supplier_scheme_management_roles",
			_SCHEME_MANAGEMENT_ROLES,
			_("close a supplier scheme"),
		)
		if self.docstatus != 1:
			frappe.throw(_("Only submitted schemes can be closed"), title=_("Supplier Scheme Circular Error"))
		self.db_set("status", "Closed")
		self.reload()
