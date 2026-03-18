"""
Accounting module for Supplier Scheme — accrual and settlement Journal Entry creation.

Accounts used:
  - Scheme Receivable: company.custom_scheme_receivable_account (or a default)
  - Rebate Income: company.custom_scheme_rebate_income_account (or default_income_account)
  - TDS Payable: company.custom_tds_payable_account (or default)
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate


def create_accrual_entry(settlement_name):
	"""
	Create an accrual Journal Entry when a claim is raised:
	  Dr  Scheme Receivable (asset)
	  Cr  Rebate Income (income)

	Links the JE back to the Scheme Settlement.
	"""
	sett = frappe.get_doc("Scheme Settlement", settlement_name)
	if sett.accrual_journal_entry:
		frappe.throw(_("Accrual JE already exists: {0}").format(sett.accrual_journal_entry))

	company = _get_company(sett)
	receivable_account = _get_account(company, "custom_scheme_receivable_account", "Scheme Receivable")
	income_account = _get_account(company, "custom_scheme_rebate_income_account", "default_income_account")
	amount = flt(sett.claim_amount)

	if amount <= 0:
		frappe.throw(_("Claim amount must be positive to create accrual entry"))

	je = frappe.new_doc("Journal Entry")
	je.company = company
	je.posting_date = sett.credit_note_date or nowdate()
	je.voucher_type = "Journal Entry"
	je.user_remark = f"Accrual for Scheme Settlement {sett.name} / Scheme {sett.scheme}"

	je.append("accounts", {
		"account": receivable_account,
		"debit_in_account_currency": amount,
		"credit_in_account_currency": 0,
	})
	je.append("accounts", {
		"account": income_account,
		"debit_in_account_currency": 0,
		"credit_in_account_currency": amount,
	})

	je.flags.ignore_permissions = True
	je.save()
	je.submit()

	frappe.db.set_value("Scheme Settlement", settlement_name, "accrual_journal_entry", je.name)
	return je.name


def create_settlement_entry(settlement_name):
	"""
	Create a settlement Journal Entry when payment/CN is received:
	  Dr  Bank/Cash or Supplier Account
	  Cr  Scheme Receivable (clear the receivable)

	If TDS is deducted:
	  Dr  TDS Payable (for the TDS portion)
	"""
	sett = frappe.get_doc("Scheme Settlement", settlement_name)
	if sett.journal_entry:
		frappe.throw(_("Settlement JE already exists: {0}").format(sett.journal_entry))

	company = _get_company(sett)
	receivable_account = _get_account(company, "custom_scheme_receivable_account", "Scheme Receivable")
	received = flt(sett.received_amount)
	tds = flt(sett.tds_deducted)
	total_credit = received + tds

	if total_credit <= 0:
		frappe.throw(_("Received amount or TDS must be positive"))

	je = frappe.new_doc("Journal Entry")
	je.company = company
	je.posting_date = sett.credit_note_date or nowdate()
	je.voucher_type = "Journal Entry"
	je.user_remark = f"Settlement for {sett.name} / Scheme {sett.scheme}"

	# Credit Scheme Receivable for total
	je.append("accounts", {
		"account": receivable_account,
		"debit_in_account_currency": 0,
		"credit_in_account_currency": total_credit,
	})

	# Debit received amount to default receivable account (supplier pays)
	default_receivable = frappe.db.get_value("Company", company, "default_receivable_account")
	if received > 0 and default_receivable:
		je.append("accounts", {
			"account": default_receivable,
			"debit_in_account_currency": received,
			"credit_in_account_currency": 0,
		})

	# Debit TDS to TDS payable
	if tds > 0:
		tds_account = _get_account(company, "custom_tds_payable_account", None)
		if tds_account:
			je.append("accounts", {
				"account": tds_account,
				"debit_in_account_currency": tds,
				"credit_in_account_currency": 0,
			})

	je.flags.ignore_permissions = True
	je.save()
	je.submit()

	frappe.db.set_value("Scheme Settlement", settlement_name, "journal_entry", je.name)
	return je.name


def reverse_settlement_entry(settlement_name):
	"""Cancel the JEs linked to a settlement (called on settlement cancel)."""
	sett = frappe.get_doc("Scheme Settlement", settlement_name)

	for field in ("journal_entry", "accrual_journal_entry"):
		je_name = sett.get(field)
		if je_name:
			je = frappe.get_doc("Journal Entry", je_name)
			if je.docstatus == 1:
				je.flags.ignore_permissions = True
				je.cancel()


# ── Helpers ──────────────────────────────────────────────────────────

def _get_company(settlement):
	"""Get company from the linked scheme."""
	company = frappe.db.get_value(
		"Supplier Scheme Circular", settlement.scheme, "company"
	)
	if not company:
		company = frappe.defaults.get_global_default("company")
	if not company:
		frappe.throw(_("Company not set on scheme {0}").format(settlement.scheme))
	return company


def _get_account(company, custom_field, fallback_field):
	"""
	Get account from Company custom field, falling back to a standard field.
	"""
	account = frappe.db.get_value("Company", company, custom_field)
	if not account and fallback_field:
		account = frappe.db.get_value("Company", company, fallback_field)
	if not account:
		frappe.throw(
			_("Please set {0} in Company {1}").format(custom_field, company)
		)
	return account
