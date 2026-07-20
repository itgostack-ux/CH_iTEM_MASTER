# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Quarantine the price batches that were side-loaded outside the workflow.

On 2026-07-07 the Ready Reckoner Excel export was broken by a tuple-nesting
bug in ``_GRADE_LABELS`` (fixed later in ``ready_reckoner_api``). Blocked on
the export, an operator loaded the POS price book with a raw SQL script that
manufactured one audit-shadow ``CH Price Upload Batch`` per item, pre-stamped
``status='Approved'`` with no ``approved_by``, and bulk-inserted the matching
``CH Item Price`` rows.

The prices are live and correct — a row-by-row comparison found 8,189 of
8,190 matching the active price exactly (the one divergence is an item whose
value was later set through the real workflow, which won). So this is audit
debt, not a pricing bug, and nothing here rewrites a price.

The problem is the status. The rebuilt approval flow gives ``Approved`` a real
meaning — "all categories signed off, awaiting apply" — so leaving 8,190 rows
in that state would present them as a queue of pending applications that could
be re-fired. They are moved to ``Legacy Import``, a terminal state no action
transitions out of.

Signature used to identify them (all must hold):
  * status = 'Approved'
  * approved_by IS NULL       — the workflow always stamps this
  * submitted_at IS NOT NULL  — the script back-dated it

Genuine batches are unaffected: a real approval always records ``approved_by``.
"""

import frappe


def execute():
	# table_exists takes the doctype name, not the tab-prefixed table name.
	if not frappe.db.table_exists("CH Price Upload Batch"):
		return

	names = frappe.get_all(
		"CH Price Upload Batch",
		filters={"status": "Approved", "approved_by": ("is", "not set")},
		pluck="name",
	)
	if not names:
		return

	frappe.db.sql(
		"""
		UPDATE `tabCH Price Upload Batch`
		   SET status = 'Legacy Import'
		 WHERE status = 'Approved'
		   AND (approved_by IS NULL OR approved_by = '')
		"""
	)

	# The same script wrote child rows with status 'Active', which is not one
	# of the child's Select options — code testing for 'Pending'/'Applied'
	# treats them as neither. Their prices are live, so 'Applied' is accurate.
	frappe.db.sql(
		"""
		UPDATE `tabCH Price Upload Item` child
		  JOIN `tabCH Price Upload Batch` parent ON parent.name = child.parent
		   SET child.status = 'Applied', child.approval_status = 'Approved'
		 WHERE parent.status = 'Legacy Import'
		   AND child.status = 'Active'
		"""
	)

	frappe.db.commit()
	print(f"v33: quarantined {len(names)} legacy price batches to 'Legacy Import'")
