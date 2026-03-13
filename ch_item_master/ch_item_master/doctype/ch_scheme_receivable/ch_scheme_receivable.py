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
from frappe.utils import flt, nowdate, getdate


class CHSchemeReceivable(Document):
	def validate(self):
		self._auto_fetch_pricing_rule()
		self._compute_outstanding()
		self._validate_amounts()
		self._update_status()

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
			frappe.throw(_("Claim Amount must be greater than zero."))
		if flt(self.received_amount) < 0:
			frappe.throw(_("Received Amount cannot be negative."))
		if flt(self.written_off_amount) < 0:
			frappe.throw(_("Written Off Amount cannot be negative."))
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

@frappe.whitelist()
def record_settlement(receivable_name, amount, payment_reference=None,
                      settlement_date=None, payment_entry=None):
	"""Record partial or full settlement against a scheme receivable.

	Args:
		receivable_name: CH Scheme Receivable name
		amount: Settlement amount received
		payment_reference: UTR or bank reference
		settlement_date: Date the money was received (default: today)
		payment_entry: Link to Payment Entry if created
	"""
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Settlement amount must be positive."))

	doc = frappe.get_doc("CH Scheme Receivable", receivable_name)
	if doc.docstatus != 1:
		frappe.throw(_("Can only settle submitted receivables."))
	if doc.status in ("Settled", "Written Off", "Cancelled"):
		frappe.throw(_("Receivable {0} is already {1}.").format(receivable_name, doc.status))

	outstanding = flt(doc.outstanding_amount)
	if amount > outstanding:
		frappe.throw(
			_("Settlement amount {0} exceeds outstanding {1}.").format(amount, outstanding)
		)

	doc.received_amount = flt(doc.received_amount) + amount
	doc.settlement_date = settlement_date or nowdate()
	if payment_reference:
		doc.payment_reference = payment_reference
	if payment_entry:
		doc.payment_entry = payment_entry

	doc.flags.ignore_validate_update_after_submit = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": doc.status, "outstanding": doc.outstanding_amount}


@frappe.whitelist()
def write_off(receivable_name, amount=None, journal_entry=None, remarks=None):
	"""Write off outstanding amount (partial or full).

	Args:
		receivable_name: CH Scheme Receivable name
		amount: Amount to write off (default: full outstanding)
		journal_entry: Link to JV if created
		remarks: Reason for write-off
	"""
	doc = frappe.get_doc("CH Scheme Receivable", receivable_name)
	if doc.docstatus != 1:
		frappe.throw(_("Can only write off submitted receivables."))

	outstanding = flt(doc.outstanding_amount)
	amount = flt(amount) if amount else outstanding
	if amount <= 0 or amount > outstanding:
		frappe.throw(_("Write-off amount must be between 0 and {0}.").format(outstanding))

	doc.written_off_amount = flt(doc.written_off_amount) + amount
	if journal_entry:
		doc.journal_entry = journal_entry
	if remarks:
		doc.remarks = (doc.remarks or "") + "\n" + remarks

	doc.flags.ignore_validate_update_after_submit = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": doc.status, "outstanding": doc.outstanding_amount}


@frappe.whitelist()
def create_from_pos_invoice(doc, method=None):
	"""Auto-detect bank/brand offers on a POS Invoice and create receivables.

	Called after POS Invoice submit (via doc_events hook) or standalone.
	Scans the invoice for offer-based discounts that generate third-party
	receivables.

	Args:
		doc: POS Invoice doc object, or POS Invoice name (string).
		method: Unused — present for doc_events compatibility.

	Returns list of created CH Scheme Receivable names.
	"""
	if isinstance(doc, str):
		inv = frappe.get_doc("POS Invoice", doc)
	else:
		inv = doc
	created = []

	# Check for offers linked to this invoice's items
	for item in inv.items:
		offer_name = item.get("custom_ch_offer") or item.get("pricing_rules")
		if not offer_name:
			continue

		# Try to resolve as CH Item Offer
		offer = _get_ch_offer(offer_name)
		if not offer:
			continue

		scheme_type = _offer_type_to_scheme(offer.offer_type)
		if not scheme_type:
			continue

		party_type, party_name = _resolve_party(offer)
		if not party_name:
			continue

		# Calculate the receivable amount for this item
		recv_amount = _compute_receivable_amount(offer, item)
		if flt(recv_amount) <= 0:
			continue

		# Check if a receivable already exists for this offer + invoice
		existing = frappe.db.exists("CH Scheme Receivable", {
			"offer_reference": offer.name,
			"docstatus": ["!=", 2],
		})

		if existing:
			# Append invoice row to existing receivable
			doc = frappe.get_doc("CH Scheme Receivable", existing)
			already_linked = any(
				r.invoice_type == "POS Invoice" and r.invoice == inv.name
				for r in doc.invoices
			)
			if not already_linked:
				doc.append("invoices", {
					"invoice_type": "POS Invoice",
					"invoice": inv.name,
					"invoice_date": inv.posting_date,
					"customer": inv.customer,
					"amount": recv_amount,
				})
				doc.claim_amount = flt(doc.claim_amount) + recv_amount
				doc.flags.ignore_validate_update_after_submit = True
				doc.save(ignore_permissions=True)
			created.append(doc.name)
		else:
			# Create new receivable
			doc = frappe.get_doc({
				"doctype": "CH Scheme Receivable",
				"scheme_type": scheme_type,
				"party_type": party_type,
				"party_name": party_name,
				"company": inv.company,
				"offer_reference": offer.name,
				"claim_amount": recv_amount,
				"claim_date": inv.posting_date,
				"invoices": [{
					"invoice_type": "POS Invoice",
					"invoice": inv.name,
					"invoice_date": inv.posting_date,
					"customer": inv.customer,
					"amount": recv_amount,
				}],
			})
			doc.insert(ignore_permissions=True)
			created.append(doc.name)

	if created:
		frappe.db.commit()

	return created


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
