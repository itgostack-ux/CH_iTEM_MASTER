"""Database-backed smoke tests for deterministic store Cost Center routing.

Run with::

	bench --site erpnext.local execute \
		ch_item_master.tests.test_store_cost_center_routing.run

The documents remain unsaved; this verifies routing against real Store,
Warehouse, POS Profile, Cost Center, and Sales Invoice data without creating
accounting transactions.
"""

import frappe

from ch_item_master.ch_core.cost_center import (
	apply_document_store_cost_center,
	resolve_cost_center,
	resolve_reference_cost_center,
)


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


def run():
	handler = "ch_item_master.ch_core.cost_center.apply_document_store_cost_center"
	doc_events = frappe.get_hooks("doc_events")
	for doctype in (
		"Purchase Invoice",
		"Purchase Receipt",
		"Stock Entry",
		"Stock Reconciliation",
		"Delivery Note",
		"Journal Entry",
		"Payment Entry",
		"Expense Claim",
	):
		configured = doc_events.get(doctype, {}).get("before_validate", [])
		if isinstance(configured, str):
			configured = [configured]
		_assert(handler in configured, f"{doctype} is not wired to store routing")

	stores = frappe.db.sql(
		"""
		SELECT s.name, s.company, s.warehouse, p.cost_center
		FROM `tabCH Store` s
		JOIN `tabPOS Profile` p ON p.name = s.pos_profile
		WHERE s.disabled = 0 AND COALESCE(s.is_hub, 0) = 0
		  AND COALESCE(s.warehouse, '') != ''
		  AND COALESCE(p.cost_center, '') != ''
		ORDER BY s.company, s.name
		""",
		as_dict=True,
	)
	_assert(stores, "No active Store/POS Profile accounting fixtures found")
	primary = stores[0]
	company_default = frappe.db.get_value(
		"Company", primary.company, "cost_center"
	)

	resolved = resolve_cost_center(
		primary.company,
		warehouse=primary.warehouse,
		fallback_to_company=False,
	)
	_assert(
		resolved == primary.cost_center,
		f"Warehouse resolved {resolved}; expected {primary.cost_center}",
	)

	# Non-POS procurement/expense posting: a store warehouse replaces blank or
	# Company-default values on both the header and the posting child row.
	purchase_invoice = frappe.new_doc("Purchase Invoice")
	purchase_invoice.company = primary.company
	purchase_invoice.set_warehouse = primary.warehouse
	purchase_invoice.cost_center = company_default
	item = purchase_invoice.append(
		"items",
		{"warehouse": primary.warehouse, "cost_center": company_default},
	)
	apply_document_store_cost_center(purchase_invoice)
	_assert(purchase_invoice.cost_center == primary.cost_center, "PI header not routed")
	_assert(item.cost_center == primary.cost_center, "PI item not routed")

	# An explicit non-default assignment is an accounting decision and must not
	# be overwritten even when the document warehouse points at another store.
	other = next(
		(
			row for row in stores
			if row.company == primary.company and row.cost_center != primary.cost_center
		),
		None,
	)
	if other:
		item.cost_center = other.cost_center
		apply_document_store_cost_center(purchase_invoice)
		_assert(item.cost_center == other.cost_center, "Explicit Cost Center was overwritten")

	# A transfer spanning two stores must remain unassigned at header level;
	# each row is attributed independently instead of misreporting the whole
	# transfer under whichever store happened to be read first.
	if other:
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = primary.company
		stock_entry.cost_center = company_default
		row_one = stock_entry.append(
			"items", {"s_warehouse": primary.warehouse, "cost_center": company_default}
		)
		row_two = stock_entry.append(
			"items", {"s_warehouse": other.warehouse, "cost_center": company_default}
		)
		apply_document_store_cost_center(stock_entry)
		_assert(stock_entry.cost_center == company_default, "Mixed-store header was forced")
		_assert(row_one.cost_center == primary.cost_center, "First transfer row not routed")
		_assert(row_two.cost_center == other.cost_center, "Second transfer row not routed")

	invoice = frappe.db.sql(
		"""
		SELECT sii.parent
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1 AND si.company = %s
		  AND sii.cost_center LIKE 'POS - %%'
		GROUP BY sii.parent
		HAVING COUNT(DISTINCT sii.cost_center) = 1
		ORDER BY si.posting_date DESC, si.creation DESC
		LIMIT 1
		""",
		primary.company,
	)
	if invoice:
		reference_cc = resolve_reference_cost_center(
			"Sales Invoice", invoice[0][0], primary.company
		)
		_assert(reference_cc, "Sales Invoice reference did not carry a store Cost Center")
		payment_entry = frappe.new_doc("Payment Entry")
		payment_entry.company = primary.company
		payment_entry.cost_center = company_default
		payment_entry.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice[0][0],
			},
		)
		apply_document_store_cost_center(payment_entry)
		_assert(
			payment_entry.cost_center == reference_cc,
			"Payment Entry did not inherit its invoice Cost Center",
		)

	result = {
		"operational_hooks": "PASS",
		"warehouse_resolution": "PASS",
		"purchase_invoice_header_and_items": "PASS",
		"explicit_assignment_preserved": "PASS" if other else "SKIP",
		"multi_store_transfer": "PASS" if other else "SKIP",
		"sales_reference_inheritance": "PASS" if invoice else "SKIP",
		"payment_entry_reference_inheritance": "PASS" if invoice else "SKIP",
		"store": primary.name,
		"cost_center": primary.cost_center,
	}
	print(result)
	return result
