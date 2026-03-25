# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHVASLedger(Document):
	pass


def log_vas_event(sold_plan, event_type, claim_amount=0,
                  reference_doctype=None, reference_name=None, remarks=None):
	"""Append an audit entry to the VAS ledger.

	Called from warranty_api / ch_warranty_claim / voucher_api whenever
	a coverage-altering event occurs.

	Args:
		sold_plan: CH Sold Plan name
		event_type: One of the Select options (Plan Activated / Claim Used / …)
		claim_amount: Monetary value of the event (claim cost, voucher value, etc.)
		reference_doctype: Source doctype (CH Warranty Claim, CH Voucher, etc.)
		reference_name: Source document name
		remarks: Free text
	"""
	sp = frappe.db.get_value(
		"CH Sold Plan", sold_plan,
		["claims_used", "max_claims", "total_claimed_value", "max_coverage_value"],
		as_dict=True,
	)
	if not sp:
		return

	remaining_claims = max(0, (sp.max_claims or 0) - (sp.claims_used or 0)) if sp.max_claims else -1
	remaining_value = max(0, (sp.max_coverage_value or 0) - (sp.total_claimed_value or 0)) if sp.max_coverage_value else -1

	doc = frappe.new_doc("CH VAS Ledger")
	doc.update({
		"sold_plan": sold_plan,
		"event_type": event_type,
		"claim_amount": claim_amount,
		"remaining_claims": remaining_claims if remaining_claims >= 0 else 0,
		"remaining_value": remaining_value if remaining_value >= 0 else 0,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"remarks": remarks or "",
	})
	doc.flags.ignore_permissions = True
	doc.insert()
