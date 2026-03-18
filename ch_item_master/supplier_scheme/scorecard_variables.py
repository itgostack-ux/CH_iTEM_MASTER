"""
Custom Supplier Scorecard Variables for CH Retail.

Each function receives a scorecard period object with:
  - scorecard.supplier  (supplier name)
  - scorecard.start_date
  - scorecard.end_date
and must return a numeric value.
"""

import frappe


def get_ch_settlement_days(scorecard):
	"""Average days between claim period end and settlement credit-note date."""
	data = frappe.db.sql(
		"""
		SELECT AVG(DATEDIFF(sset.credit_note_date, sscl.period_to))
		FROM `tabScheme Settlement` sset
		JOIN `tabScheme Claim Summary` sscl ON sscl.name = sset.claim_summary
		JOIN `tabSupplier Scheme Circular` ssc ON ssc.name = sset.scheme
		WHERE ssc.supplier = %(supplier)s
			AND sset.docstatus = 1
			AND sset.credit_note_date BETWEEN %(start_date)s AND %(end_date)s
		""",
		{
			"supplier": scorecard.supplier,
			"start_date": scorecard.start_date,
			"end_date": scorecard.end_date,
		},
		as_dict=0,
	)[0][0]
	return data or 0


def get_ch_recovery_rate(scorecard):
	"""Percentage of claimed amount actually received via settlements.

	Formula: SUM(received_amount) / SUM(claim_amount) * 100
	Returns 100 if no claims exist (ideal state).
	"""
	data = frappe.db.sql(
		"""
		SELECT
			SUM(sset.received_amount) as received,
			SUM(sset.claim_amount) as claimed
		FROM `tabScheme Settlement` sset
		JOIN `tabSupplier Scheme Circular` ssc ON ssc.name = sset.scheme
		WHERE ssc.supplier = %(supplier)s
			AND sset.docstatus = 1
			AND sset.credit_note_date BETWEEN %(start_date)s AND %(end_date)s
		""",
		{
			"supplier": scorecard.supplier,
			"start_date": scorecard.start_date,
			"end_date": scorecard.end_date,
		},
		as_dict=1,
	)[0]

	if not data.claimed:
		return 100
	return (data.received or 0) / data.claimed * 100


def get_ch_warranty_compliance(scorecard):
	"""Percentage of achievement entries eligible for payout.

	Formula: COUNT(eligible_for_payout=1) / COUNT(*) * 100
	Returns 100 if no entries exist (ideal state).
	"""
	data = frappe.db.sql(
		"""
		SELECT
			COUNT(*) as total,
			SUM(CASE WHEN sal.eligible_for_payout = 1 THEN 1 ELSE 0 END) as eligible
		FROM `tabScheme Achievement Ledger` sal
		JOIN `tabSupplier Scheme Circular` ssc ON ssc.name = sal.scheme
		WHERE ssc.supplier = %(supplier)s
			AND sal.invoice_date BETWEEN %(start_date)s AND %(end_date)s
		""",
		{
			"supplier": scorecard.supplier,
			"start_date": scorecard.start_date,
			"end_date": scorecard.end_date,
		},
		as_dict=1,
	)[0]

	if not data.total:
		return 100
	return (data.eligible or 0) / data.total * 100


def get_ch_defect_rate(scorecard):
	"""Percentage of received items returned as defective.

	Formula: Purchase Return qty / Purchase Receipt qty * 100
	Returns 0 if no receipts exist (ideal state).
	"""
	received = frappe.db.sql(
		"""
		SELECT SUM(pri.qty)
		FROM `tabPurchase Receipt Item` pri
		JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pr.supplier = %(supplier)s
			AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
			AND pr.docstatus = 1
			AND pr.is_return = 0
		""",
		{
			"supplier": scorecard.supplier,
			"start_date": scorecard.start_date,
			"end_date": scorecard.end_date,
		},
	)[0][0]

	if not received:
		return 0

	returned = frappe.db.sql(
		"""
		SELECT ABS(SUM(pri.qty))
		FROM `tabPurchase Receipt Item` pri
		JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pr.supplier = %(supplier)s
			AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
			AND pr.docstatus = 1
			AND pr.is_return = 1
		""",
		{
			"supplier": scorecard.supplier,
			"start_date": scorecard.start_date,
			"end_date": scorecard.end_date,
		},
	)[0][0]

	return (returned or 0) / received * 100


def get_ch_scheme_frequency(scorecard):
	"""Count of active supplier schemes overlapping the scoring period."""
	data = frappe.db.sql(
		"""
		SELECT COUNT(*)
		FROM `tabSupplier Scheme Circular` ssc
		WHERE ssc.supplier = %(supplier)s
			AND ssc.docstatus = 1
			AND ssc.valid_from <= %(end_date)s
			AND ssc.valid_to >= %(start_date)s
		""",
		{
			"supplier": scorecard.supplier,
			"start_date": scorecard.start_date,
			"end_date": scorecard.end_date,
		},
	)[0][0]
	return data or 0
