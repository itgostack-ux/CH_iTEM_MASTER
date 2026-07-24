# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
CH Scheme Receivable — tracks money owed by banks or brands
for applied offers (bank cashback, EMI subvention, brand co-op).

Lifecycle:  Pending → Claimed → Partially Received → Settled
                                                  ↘ Written Off
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, date_diff, flt, getdate, nowdate, validate_email_address

from ch_item_master.config import get_int_setting, is_privileged_user, require_role_setting
from ch_item_master.security import ensure_company_access, require_scoped_document_action


_SETTLEMENT_ROLES = ("Accounts Manager", "Purchase Manager", "Scheme Manager")
_WRITE_OFF_ROLES = ("Accounts Manager",)
_SCHEME_MANAGEMENT_ROLES = ("Accounts Manager", "Purchase Manager", "Scheme Manager")
_CURRENCY_TOLERANCE = 0.01


class CHSchemeReceivable(Document):
	def validate(self):
		self._validate_financial_mutation()
		self._auto_fetch_pricing_rule()
		self._compute_outstanding()
		self._validate_amounts()
		self._update_status()

	def _validate_financial_mutation(self):
		if self.flags.get("ch_scheme_financial_action"):
			return
		fields = (
			"received_amount",
			"written_off_amount",
			"settlement_date",
			"payment_reference",
			"payment_entry",
			"journal_entry",
		)
		if self.is_new():
			changed = any(
				flt(self.get(field)) if field in ("received_amount", "written_off_amount") else self.get(field)
				for field in fields
			)
		else:
			before = frappe.db.get_value(self.doctype, self.name, fields, as_dict=True) or {}
			changed = any(
				abs(flt(self.get(field)) - flt(before.get(field))) > _CURRENCY_TOLERANCE
				if field in ("received_amount", "written_off_amount")
				else (self.get(field) or None) != (before.get(field) or None)
				for field in fields
			)
		if changed:
			frappe.throw(
				_("Settlement and write-off fields can only be changed through their verified actions."),
				frappe.PermissionError,
			)

	def _auto_fetch_pricing_rule(self):
		"""Auto-populate pricing_rule from linked CH Item Offer."""
		if self.offer_reference and not self.pricing_rule:
			self.pricing_rule = frappe.db.get_value(
				"CH Item Offer", self.offer_reference, "erp_pricing_rule"
			)

	def on_submit(self):
		if self.status == "Pending":
			self.db_set("status", "Claimed")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def _compute_outstanding(self):
		self.outstanding_amount = flt(self.claim_amount) - flt(self.received_amount) - flt(self.written_off_amount)

	def _validate_amounts(self):
		if flt(self.claim_amount) <= 0:
			frappe.throw(_("Claim Amount must be greater than zero."), title=_("Ch Scheme Receivable Error"))
		if flt(self.received_amount) < 0:
			frappe.throw(_("Received Amount cannot be negative."), title=_("Ch Scheme Receivable Error"))
		if flt(self.written_off_amount) < 0:
			frappe.throw(_("Written Off Amount cannot be negative."), title=_("Ch Scheme Receivable Error"))
		if flt(self.outstanding_amount) < 0:
			frappe.throw(
				_("Received + Written Off ({0}) exceeds Claim Amount ({1}).").format(
					flt(self.received_amount) + flt(self.written_off_amount),
					self.claim_amount,
				)
			)

	def _update_status(self):
		if self.docstatus == 2:
			return
		outstanding = flt(self.outstanding_amount)
		received = flt(self.received_amount)
		written_off = flt(self.written_off_amount)

		if written_off >= flt(self.claim_amount):
			self.status = "Written Off"
		elif outstanding <= 0 and received > 0:
			self.status = "Settled"
		elif received > 0 and outstanding > 0:
			self.status = "Partially Received"
		elif self.docstatus == 1:
			self.status = "Claimed"
		else:
			self.status = "Pending"


# ── Whitelisted API ─────────────────────────────────────────────────────────

def _bounded_text(value, label, setting, default, required=False):
	value = (value or "").strip()
	if required and not value:
		frappe.throw(_("{0} is required.").format(label), frappe.ValidationError)
	limit = get_int_setting(setting, default, minimum=1)
	if len(value) > limit:
		frappe.throw(
			_("{0} cannot exceed {1} characters.").format(label, limit),
			frappe.ValidationError,
		)
	return value


def _validate_action_amount(amount, outstanding):
	amount = flt(amount)
	limit = flt(get_int_setting("scheme_receivable_action_amount_limit", 10_000_000, minimum=1))
	if amount <= 0 or amount > flt(outstanding):
		frappe.throw(
			_("Amount must be greater than zero and cannot exceed the outstanding amount of {0}.").format(
				flt(outstanding)
			),
			frappe.ValidationError,
		)
	if amount > limit:
		frappe.throw(
			_("Amount exceeds the configured per-action limit of {0}.").format(limit),
			frappe.ValidationError,
		)
	return amount


def _require_reference_read(doc):
	if is_privileged_user():
		return
	if not frappe.has_permission(doc.doctype, "read", doc=doc, throw=False):
		frappe.throw(
			_("You do not have permission to use {0} {1}.").format(doc.doctype, doc.name),
			frappe.PermissionError,
		)


def _ensure_reference_is_unused(receivable, fieldname, reference):
	existing = frappe.db.get_value(
		"CH Scheme Receivable",
		{
			fieldname: reference,
			"name": ("!=", receivable.name),
			"docstatus": ("!=", 2),
		},
		"name",
	)
	if existing:
		frappe.throw(
			_("{0} is already linked to scheme receivable {1}.").format(reference, existing),
			frappe.ValidationError,
		)


def _validate_payment_entry(receivable, payment_entry, amount, payment_reference, settlement_date):
	if receivable.payment_entry and receivable.payment_entry != payment_entry:
		frappe.throw(_("This receivable is already linked to another Payment Entry."), frappe.ValidationError)

	entry = frappe.get_doc("Payment Entry", payment_entry)
	_require_reference_read(entry)
	if entry.docstatus != 1:
		frappe.throw(_("Payment Entry must be submitted."), frappe.ValidationError)
	if entry.company != receivable.company:
		frappe.throw(_("Payment Entry company does not match the receivable."), frappe.ValidationError)
	if entry.payment_type != "Receive":
		frappe.throw(_("Payment Entry must be a receipt."), frappe.ValidationError)
	if entry.party_type != receivable.party_type or entry.party != receivable.party_name:
		frappe.throw(_("Payment Entry party does not match the receivable party."), frappe.ValidationError)
	if abs(flt(entry.base_received_amount) - amount) > _CURRENCY_TOLERANCE:
		frappe.throw(_("Payment Entry amount does not match the settlement amount."), frappe.ValidationError)

	expected_reference = _bounded_text(
		entry.reference_no or entry.name,
		_("Payment reference"),
		"scheme_receivable_reference_limit",
		140,
		required=True,
	)
	if payment_reference and payment_reference != expected_reference:
		frappe.throw(_("Payment reference does not match the Payment Entry."), frappe.ValidationError)
	if settlement_date and getdate(settlement_date) != getdate(entry.posting_date):
		frappe.throw(_("Settlement date must match the Payment Entry posting date."), frappe.ValidationError)

	_ensure_reference_is_unused(receivable, "payment_entry", entry.name)
	return entry.name, expected_reference, entry.posting_date


def _validate_journal_entry(receivable, journal_entry, amount):
	if receivable.journal_entry and receivable.journal_entry != journal_entry:
		frappe.throw(_("This receivable is already linked to another Journal Entry."), frappe.ValidationError)

	entry = frappe.get_doc("Journal Entry", journal_entry)
	_require_reference_read(entry)
	if entry.docstatus != 1:
		frappe.throw(_("Journal Entry must be submitted."), frappe.ValidationError)
	if entry.company != receivable.company:
		frappe.throw(_("Journal Entry company does not match the receivable."), frappe.ValidationError)
	if abs(flt(entry.total_debit) - amount) > _CURRENCY_TOLERANCE or abs(
		flt(entry.total_credit) - amount
	) > _CURRENCY_TOLERANCE:
		frappe.throw(_("Journal Entry totals do not match the write-off amount."), frappe.ValidationError)

	party_rows = [
		row
		for row in entry.accounts
		if row.party_type == receivable.party_type and row.party == receivable.party_name
	]
	party_debit = sum(flt(row.debit) for row in party_rows)
	party_credit = sum(flt(row.credit) for row in party_rows)
	if not party_rows or (
		abs(party_debit - amount) > _CURRENCY_TOLERANCE
		and abs(party_credit - amount) > _CURRENCY_TOLERANCE
	):
		frappe.throw(_("Journal Entry party and amount do not match the receivable."), frappe.ValidationError)
	if getdate(entry.posting_date) > getdate(nowdate()):
		frappe.throw(_("Journal Entry posting date cannot be in the future."), frappe.ValidationError)

	_ensure_reference_is_unused(receivable, "journal_entry", entry.name)
	return entry.name


@frappe.whitelist(methods=["POST"])
def record_settlement(receivable_name, amount, payment_reference=None,
                      settlement_date=None, payment_entry=None) -> dict:
	"""Record partial or full settlement against a scheme receivable.

	Args:
		receivable_name: CH Scheme Receivable name
		amount: Settlement amount received
		payment_reference: UTR or bank reference
		settlement_date: Date the money was received (default: today)
		payment_entry: Link to Payment Entry if created
	"""
	doc = frappe.get_doc("CH Scheme Receivable", receivable_name)
	require_scoped_document_action(
		doc,
		"scheme_receivable_settlement_roles",
		_SETTLEMENT_ROLES,
		action=_("record a scheme receivable settlement"),
		permission_types=("write",),
		store_field="store",
		lock=True,
	)
	if doc.docstatus != 1:
		frappe.throw(_("Can only settle submitted receivables."), title=_("Ch Scheme Receivable Error"))
	if doc.status in ("Settled", "Written Off", "Cancelled"):
		frappe.throw(_("Receivable {0} is already {1}.").format(receivable_name, doc.status), title=_("Ch Scheme Receivable Error"))

	outstanding = flt(doc.outstanding_amount)
	amount = _validate_action_amount(amount, outstanding)
	payment_reference = _bounded_text(
		payment_reference,
		_("Payment reference"),
		"scheme_receivable_reference_limit",
		140,
		required=not payment_entry,
	)
	if payment_entry:
		payment_entry, payment_reference, settlement_date = _validate_payment_entry(
			doc,
			payment_entry,
			amount,
			payment_reference,
			settlement_date,
		)
	else:
		settlement_date = settlement_date or nowdate()
		if getdate(settlement_date) > getdate(nowdate()):
			frappe.throw(_("Settlement date cannot be in the future."), frappe.ValidationError)
		existing = frappe.db.get_value(
			"CH Scheme Receivable",
			{
				"company": doc.company,
				"payment_reference": payment_reference,
				"name": ("!=", doc.name),
				"docstatus": ("!=", 2),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("Payment reference is already linked to scheme receivable {0}.").format(existing),
				frappe.ValidationError,
			)

	doc.received_amount = flt(doc.received_amount) + amount
	doc.settlement_date = settlement_date
	doc.payment_reference = payment_reference
	if payment_entry:
		doc.payment_entry = payment_entry

	doc._compute_outstanding()
	doc._update_status()
	doc.flags.ch_scheme_financial_action = True
	doc.flags.ignore_validate_update_after_submit = True
	doc.save()

	return {"status": doc.status, "outstanding": doc.outstanding_amount}


@frappe.whitelist(methods=["POST"])
def write_off(receivable_name, amount=None, journal_entry=None, remarks=None) -> dict:
	"""Write off outstanding amount (partial or full).

	Args:
		receivable_name: CH Scheme Receivable name
		amount: Amount to write off (default: full outstanding)
		journal_entry: Link to JV if created
		remarks: Reason for write-off
	"""
	doc = frappe.get_doc("CH Scheme Receivable", receivable_name)
	require_scoped_document_action(
		doc,
		"scheme_receivable_write_off_roles",
		_WRITE_OFF_ROLES,
		action=_("write off a scheme receivable"),
		permission_types=("write",),
		store_field="store",
		lock=True,
	)
	if doc.docstatus != 1:
		frappe.throw(_("Can only write off submitted receivables."), title=_("Ch Scheme Receivable Error"))
	if doc.status in ("Settled", "Written Off", "Cancelled"):
		frappe.throw(_("Receivable {0} is already {1}.").format(receivable_name, doc.status), title=_("Ch Scheme Receivable Error"))

	outstanding = flt(doc.outstanding_amount)
	amount = flt(amount) if amount else outstanding
	amount = _validate_action_amount(amount, outstanding)
	remarks = _bounded_text(
		remarks,
		_("Write-off reason"),
		"scheme_receivable_reason_limit",
		1000,
		required=True,
	)
	if not journal_entry:
		frappe.throw(_("A submitted Journal Entry is required for a write-off."), frappe.ValidationError)
	journal_entry = _validate_journal_entry(doc, journal_entry, amount)

	doc.written_off_amount = flt(doc.written_off_amount) + amount
	doc.journal_entry = journal_entry
	doc.remarks = "\n".join(filter(None, ((doc.remarks or "").strip(), remarks)))

	doc._compute_outstanding()
	doc._update_status()
	doc.flags.ch_scheme_financial_action = True
	doc.flags.ignore_validate_update_after_submit = True
	doc.save()

	# Audit
	try:
		from ch_pos.audit import log_business_event
		log_business_event(
			event_type="Scheme Write-Off",
			ref_doctype="CH Scheme Receivable", ref_name=doc.name,
			before=f"Outstanding ₹{outstanding}",
			after=f"Write-off ₹{amount}",
			remarks=remarks or "Manual write-off",
			company=doc.company,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Audit log failed for write_off {doc.name}")

	return {"status": doc.status, "outstanding": doc.outstanding_amount}


# ── Dunning ──────────────────────────────────────────────────────────────────

def _get_receivable_party_email(doc):
	contact_names = frappe.db.sql_list(
		"""
		SELECT contact.name
		FROM `tabContact` contact
		INNER JOIN `tabDynamic Link` link
			ON link.parent = contact.name
			AND link.parenttype = 'Contact'
		WHERE link.link_doctype = %(party_type)s
			AND link.link_name = %(party_name)s
			AND COALESCE(contact.email_id, '') != ''
		ORDER BY contact.is_primary_contact DESC, contact.modified DESC
		LIMIT 1
		""",
		{"party_type": doc.party_type, "party_name": doc.party_name},
	)
	if not contact_names:
		fallback = frappe.db.get_value(
			"Contact",
			{"company_name": doc.party_name, "email_id": ("is", "set")},
			"name",
			order_by="is_primary_contact desc, modified desc",
		)
		if fallback:
			contact_names = [fallback]
	if not contact_names:
		return ""

	contact = frappe.get_doc("Contact", contact_names[0])
	contact.check_permission("read")
	return validate_email_address(contact.email_id, throw=True) if contact.email_id else ""


@frappe.whitelist(methods=["POST"])
def send_dunning_notice(receivable_name) -> dict:
	"""Queue one idempotent dunning email for a scoped overdue receivable."""
	doc = frappe.get_doc("CH Scheme Receivable", receivable_name)
	require_scoped_document_action(
		doc,
		"scheme_receivable_settlement_roles",
		_SETTLEMENT_ROLES,
		action=_("send a scheme receivable dunning notice"),
		permission_types=("read", "write"),
		store_field="store",
		lock=True,
	)
	if doc.docstatus != 1:
		frappe.throw(_("Can only send dunning for submitted receivables"), title=_("Ch Scheme Receivable Error"))
	if doc.status in ("Settled", "Written Off", "Cancelled"):
		frappe.throw(_("Receivable is already {0}").format(doc.status), title=_("Ch Scheme Receivable Error"))

	outstanding = flt(doc.outstanding_amount)
	if outstanding <= 0:
		frappe.throw(_("No outstanding amount on {0}").format(receivable_name), title=_("Ch Scheme Receivable Error"))

	due_date = doc.due_date or doc.claim_date
	days_overdue = date_diff(nowdate(), str(due_date)) if due_date else 0
	if not due_date or days_overdue <= 0:
		frappe.throw(_("Receivable {0} is not overdue.").format(receivable_name), frappe.ValidationError)

	interval_days = get_int_setting("scheme_dunning_interval_days", 7, minimum=1)
	if doc.last_dunning_date:
		next_allowed_on = getdate(add_days(doc.last_dunning_date, interval_days))
		if getdate(nowdate()) < next_allowed_on:
			return {
				"sent_to": None,
				"days_overdue": days_overdue,
				"already_sent": True,
				"next_allowed_on": str(next_allowed_on),
			}

	party_email = _get_receivable_party_email(doc)
	if not party_email:
		frappe.msgprint(
			_("No email found for party '{0}'. Dunning not sent.").format(doc.party_name),
			indicator="orange",
		)
		return {"sent_to": None, "days_overdue": days_overdue, "already_sent": False}

	party_name = _bounded_text(
		doc.party_name, _("Party name"), "scheme_receivable_reference_limit", 140, required=True
	)
	company_name = _bounded_text(
		frappe.db.get_value("Company", doc.company, "company_name") or doc.company,
		_("Company name"),
		"scheme_receivable_reason_limit",
		1000,
		required=True,
	)
	scheme_type = _bounded_text(
		doc.scheme_type or "Scheme",
		_("Scheme type"),
		"scheme_receivable_reference_limit",
		140,
		required=True,
	)
	receivable_reference = _bounded_text(
		receivable_name,
		_("Receivable reference"),
		"scheme_receivable_reference_limit",
		140,
		required=True,
	)
	subject = _("Payment Due: {0} Claim #{1}").format(scheme_type, receivable_reference)
	claim_url = frappe.utils.get_url_to_form("CH Scheme Receivable", receivable_name)
	message = (
		"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:700px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
		f"<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>{frappe.utils.escape_html(company_name)} - Receivable Reminder</div>"
		"<div style='padding:16px'>"
		f"<p>Dear {frappe.utils.escape_html(party_name)},</p>"
		"<p>This is a reminder that the following claim is outstanding:</p>"
		f"<p><b>Receivable:</b> {frappe.utils.escape_html(receivable_reference)}<br>"
		f"<b>Scheme Type:</b> {frappe.utils.escape_html(scheme_type)}<br>"
		f"<b>Claim Amount:</b> ₹{flt(doc.claim_amount):,.2f}<br>"
		f"<b>Received:</b> ₹{flt(doc.received_amount):,.2f}<br>"
		f"<b>Outstanding:</b> ₹{outstanding:,.2f}<br>"
		f"<b>Due Date:</b> {due_date}<br>"
		f"<b>Days Overdue:</b> {days_overdue}</p>"
		"<p>Please arrange payment and share the UTR/reference.</p>"
		f"<p><a href='{frappe.utils.escape_html(claim_url)}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Receivable</a></p>"
		"</div></div>"
	)

	try:
		frappe.sendmail(
			recipients=[party_email],
			subject=subject,
			message=message,
			reference_doctype="CH Scheme Receivable",
			reference_name=receivable_name,
			delayed=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Dunning send failed: {receivable_name}")
		return {"sent_to": None, "days_overdue": days_overdue, "already_sent": False}

	doc.last_dunning_date = nowdate()
	doc.save()
	return {"sent_to": party_email, "days_overdue": days_overdue, "already_sent": False}


def run_scheduled_dunning():
	"""Weekly scheduled job — send dunning notices for all overdue receivables.

	Uses the configured resend interval and batch limit.
	"""
	interval_days = get_int_setting("scheme_dunning_interval_days", 7, minimum=1)
	cutoff = add_days(nowdate(), -interval_days)
	batch_limit = min(get_int_setting("supplier_scheme_link_limit", 200, minimum=1), 1000)
	overdue = frappe.db.sql("""
		SELECT name
		FROM `tabCH Scheme Receivable`
		WHERE docstatus = 1
		  AND status NOT IN ('Settled', 'Written Off', 'Cancelled')
		  AND outstanding_amount > 0
		  AND due_date < CURDATE()
		  AND (last_dunning_date IS NULL OR last_dunning_date <= %(cutoff)s)
		ORDER BY due_date ASC, name ASC
		LIMIT %(batch_limit)s
	""", {"cutoff": cutoff, "batch_limit": batch_limit}, as_dict=True)

	sent = 0
	for row in overdue:
		try:
			result = send_dunning_notice(row.name)
			if result and result.get("sent_to"):
				sent += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Dunning failed: {row.name}")

	frappe.logger("ch_item_master").info(
		f"Dunning scheduled job: {sent}/{len(overdue)} notices sent"
	)


_INVOICE_DOCTYPES = ("POS Invoice", "Sales Invoice")


def _get_locked_invoice(doc, invoice_doctype=None):
	if isinstance(doc, str):
		if invoice_doctype:
			if invoice_doctype not in _INVOICE_DOCTYPES:
				frappe.throw(_("Unsupported invoice type."), frappe.ValidationError)
			matches = [invoice_doctype] if frappe.db.exists(invoice_doctype, doc) else []
		else:
			matches = [doctype for doctype in _INVOICE_DOCTYPES if frappe.db.exists(doctype, doc)]
		if not matches:
			frappe.throw(_("Invoice {0} was not found.").format(doc), frappe.DoesNotExistError)
		if len(matches) != 1:
			frappe.throw(_("Invoice type is required for an ambiguous invoice name."), frappe.ValidationError)
		invoice = frappe.get_doc(matches[0], doc)
	else:
		invoice = doc

	if not invoice or invoice.doctype not in _INVOICE_DOCTYPES or not invoice.name:
		frappe.throw(_("A saved POS Invoice or Sales Invoice is required."), frappe.ValidationError)
	invoice.check_permission("read")
	locked_name = frappe.db.get_value(invoice.doctype, invoice.name, "name", for_update=True)
	if not locked_name:
		frappe.throw(_("Invoice {0} no longer exists.").format(invoice.name), frappe.DoesNotExistError)
	invoice.reload()
	invoice.check_permission("read")
	if invoice.docstatus != 1:
		frappe.throw(_("Only submitted invoices can create scheme receivables."), frappe.ValidationError)
	if not invoice.company or not invoice.customer or not invoice.posting_date:
		frappe.throw(_("Invoice company, customer and posting date are required."), frappe.ValidationError)
	return invoice


def _get_invoice_scope_anchors(invoice):
	profile = None
	warehouse = invoice.get("set_warehouse")
	store = None
	if invoice.get("pos_profile"):
		profile = frappe.get_doc("POS Profile", invoice.pos_profile)
		profile.check_permission("read")
		if profile.company != invoice.company:
			frappe.throw(_("Invoice POS Profile belongs to another company."), frappe.ValidationError)
		if warehouse and profile.warehouse and warehouse != profile.warehouse:
			frappe.throw(_("Invoice warehouse does not match its POS Profile."), frappe.ValidationError)
		warehouse = profile.warehouse or warehouse
		store = frappe.db.get_value("CH Store", {"pos_profile": profile.name}, "name")
		if not store and frappe.db.exists("DocType", "POS Profile Extension"):
			store = frappe.db.get_value(
				"POS Profile Extension", {"pos_profile": profile.name}, "store"
			)
	if not store and warehouse:
		store = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")

	if warehouse:
		warehouse_doc = frappe.get_doc("Warehouse", warehouse)
		warehouse_doc.check_permission("read")
		if warehouse_doc.company != invoice.company:
			frappe.throw(_("Invoice warehouse belongs to another company."), frappe.ValidationError)
	if store:
		store_doc = frappe.get_doc("CH Store", store)
		store_doc.check_permission("read")
		if store_doc.company != invoice.company:
			frappe.throw(_("Invoice store belongs to another company."), frappe.ValidationError)
		if warehouse and store_doc.warehouse and store_doc.warehouse != warehouse:
			frappe.throw(_("Invoice store and warehouse do not match."), frappe.ValidationError)

	ensure_company_access(invoice.company)
	try:
		from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
	except (ImportError, ModuleNotFoundError):
		frappe.throw(_("Location scope validation is unavailable."), frappe.PermissionError)
	assert_user_has_store_scope(
		store=store,
		warehouse=warehouse,
		company=invoice.company,
		msg=_("You are not permitted to process this invoice location."),
	)
	return store


def _iter_offer_references(item):
	references = []
	direct = (item.get("custom_ch_offer") or "").strip()
	if direct:
		references.append(direct)
	pricing_rules = item.get("pricing_rules") or ""
	for value in str(pricing_rules).replace("\n", ",").split(","):
		value = value.strip()
		if value:
			references.append(value)
	return tuple(dict.fromkeys(references))


def _validate_offer_for_invoice(offer, invoice):
	offer.check_permission("read")
	if offer.status != "Active" or offer.approval_status != "Approved":
		frappe.throw(_("Offer {0} is not active and approved.").format(offer.name), frappe.ValidationError)
	allowed_companies = {offer.company}
	allowed_companies.update(row.company for row in (offer.additional_companies or []) if row.company)
	if invoice.company not in allowed_companies:
		frappe.throw(_("Offer {0} does not apply to the invoice company.").format(offer.name), frappe.ValidationError)
	posting_date = getdate(invoice.posting_date)
	if offer.start_date and posting_date < getdate(offer.start_date):
		frappe.throw(_("Offer {0} starts after the invoice date.").format(offer.name), frappe.ValidationError)
	if offer.end_date and posting_date > getdate(offer.end_date):
		frappe.throw(_("Offer {0} expired before the invoice date.").format(offer.name), frappe.ValidationError)


def _get_existing_receivable(offer, invoice, store, party_type, party_name):
	filters = {
		"offer_reference": offer.name,
		"company": invoice.company,
		"store": store if store else ("is", "not set"),
		"party_type": party_type,
		"party_name": party_name,
		"docstatus": ("!=", 2),
	}
	names = frappe.get_all(
		"CH Scheme Receivable", filters=filters, pluck="name", order_by="creation asc", limit_page_length=2
	)
	if len(names) > 1:
		frappe.throw(_("Duplicate active receivables exist for offer {0}.").format(offer.name), frappe.ValidationError)
	if not names:
		return None
	frappe.db.get_value("CH Scheme Receivable", names[0], "name", for_update=True)
	receivable = frappe.get_doc("CH Scheme Receivable", names[0])
	receivable.check_permission("write")
	if (
		receivable.company != invoice.company
		or (receivable.store or None) != (store or None)
		or receivable.offer_reference != offer.name
		or receivable.party_type != party_type
		or receivable.party_name != party_name
	):
		frappe.throw(_("Scheme receivable reference integrity check failed."), frappe.ValidationError)
	return receivable


def _link_invoice_to_receivable(receivable, invoice, amount):
	linked_rows = [
		row
		for row in receivable.invoices
		if row.invoice_type == invoice.doctype and row.invoice == invoice.name
	]
	if len(linked_rows) > 1:
		frappe.throw(_("Invoice is linked to the receivable more than once."), frappe.ValidationError)
	if linked_rows:
		row = linked_rows[0]
		if row.customer != invoice.customer or abs(flt(row.amount) - amount) > _CURRENCY_TOLERANCE:
			frappe.throw(_("Existing invoice link does not match the submitted invoice."), frappe.ValidationError)
		return False

	receivable.append(
		"invoices",
		{
			"invoice_type": invoice.doctype,
			"invoice": invoice.name,
			"invoice_date": invoice.posting_date,
			"customer": invoice.customer,
			"amount": amount,
		},
	)
	receivable.claim_amount = flt(receivable.claim_amount) + amount
	if receivable.docstatus == 1:
		receivable.flags.ignore_validate_update_after_submit = True
	receivable.save()
	return True


def _create_from_pos_invoice(doc, method=None, invoice_doctype=None) -> list[str]:
	invoice = _get_locked_invoice(doc, invoice_doctype=invoice_doctype)
	store = _get_invoice_scope_anchors(invoice)
	amounts_by_offer = {}
	offers = {}
	for item in invoice.items:
		for offer_reference in _iter_offer_references(item):
			offer = _get_ch_offer(offer_reference)
			if not offer:
				continue
			_validate_offer_for_invoice(offer, invoice)
			amount = flt(_compute_receivable_amount(offer, item))
			if amount <= 0:
				continue
			offers[offer.name] = offer
			amounts_by_offer[offer.name] = flt(amounts_by_offer.get(offer.name)) + amount

	if not amounts_by_offer:
		return []
	created = []
	amount_limit = flt(get_int_setting("scheme_receivable_action_amount_limit", 10_000_000, minimum=1))
	for offer_name in sorted(amounts_by_offer):
		offer = offers[offer_name]
		amount = flt(amounts_by_offer[offer_name])
		if amount > amount_limit:
			frappe.throw(_("Offer receivable amount exceeds the configured limit."), frappe.ValidationError)
		scheme_type = _offer_type_to_scheme(offer.offer_type)
		party_type, party_name = _resolve_party(offer)
		if not scheme_type or not party_name:
			frappe.throw(_("Offer {0} is missing receivable configuration.").format(offer.name), frappe.ValidationError)

		receivable = _get_existing_receivable(
			offer, invoice, store, party_type, party_name
		)
		if receivable:
			_link_invoice_to_receivable(receivable, invoice, amount)
		else:
			frappe.has_permission("CH Scheme Receivable", ptype="create", throw=True)
			receivable = frappe.new_doc("CH Scheme Receivable")
			receivable.scheme_type = scheme_type
			receivable.party_type = party_type
			receivable.party_name = party_name
			receivable.company = invoice.company
			receivable.store = store
			receivable.offer_reference = offer.name
			receivable.claim_amount = amount
			receivable.claim_date = invoice.posting_date
			receivable.append(
				"invoices",
				{
					"invoice_type": invoice.doctype,
					"invoice": invoice.name,
					"invoice_date": invoice.posting_date,
					"customer": invoice.customer,
					"amount": amount,
				},
			)
			receivable.insert()
		created.append(receivable.name)
	return created


@frappe.whitelist(methods=["POST"])
def create_from_pos_invoice(doc, method=None, invoice_doctype=None) -> list[str]:
	"""Manually rebuild receivables under the configured scheme-management policy."""
	require_role_setting(
		"supplier_scheme_management_roles",
		_SCHEME_MANAGEMENT_ROLES,
		action=_("create scheme receivables from an invoice"),
	)
	return _create_from_pos_invoice(doc, method=method, invoice_doctype=invoice_doctype)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _get_ch_offer(offer_ref):
	"""Resolve offer reference to a CH Item Offer doc, returns None if not found."""
	if not offer_ref:
		return None
	# offer_ref might be a comma-separated list of pricing rule names
	# or a direct CH Item Offer name
	if frappe.db.exists("CH Item Offer", offer_ref):
		return frappe.get_doc("CH Item Offer", offer_ref)

	# Try reverse lookup: CH Item Offer with erp_pricing_rule = offer_ref
	name = frappe.db.get_value(
		"CH Item Offer",
		{"erp_pricing_rule": offer_ref, "status": "Active"},
		"name",
	)
	if name:
		return frappe.get_doc("CH Item Offer", name)
	return None


def _offer_type_to_scheme(offer_type):
	"""Map CH Item Offer.offer_type to CH Scheme Receivable.scheme_type."""
	mapping = {
		"Bank Offer": "Bank Offer",
		"Brand Offer": "Brand Co-op",
		"Cashback": "Brand Cashback",
	}
	return mapping.get(offer_type)


def _resolve_party(offer):
	"""Determine party_type and party_name from an offer."""
	if offer.offer_type == "Bank Offer":
		return "Bank", offer.bank_name or "Unknown Bank"
	elif offer.offer_type in ("Brand Offer", "Cashback"):
		brand = offer.target_brand
		if brand:
			return "Supplier", brand
		# Try to get brand from item
		if offer.item_code:
			brand = frappe.db.get_value("Item", offer.item_code, "brand")
			if brand:
				return "Supplier", brand
	return None, None


def _compute_receivable_amount(offer, invoice_item):
	"""Calculate how much the third party owes for this invoice item."""
	if offer.value_type == "Amount":
		return flt(offer.value)
	elif offer.value_type == "Percentage":
		base = flt(invoice_item.get("price_list_rate") or invoice_item.get("rate"))
		qty = flt(invoice_item.get("qty")) or 1
		return flt(base * qty * flt(offer.value) / 100)
	elif offer.value_type == "Price Override":
		original = flt(invoice_item.get("price_list_rate"))
		override = flt(offer.value)
		qty = flt(invoice_item.get("qty")) or 1
		if original > override:
			return flt((original - override) * qty)
	return 0
