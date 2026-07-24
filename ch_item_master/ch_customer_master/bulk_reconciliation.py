"""Bulk-safe customer activity reconciliation for historic Sales Invoice imports."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from typing import Any

import frappe
from frappe.utils import getdate, now_datetime


DATA_IMPORT_METHOD = "frappe.core.doctype.data_import.data_import.start_import"
RECONCILE_METHOD = (
	"ch_item_master.ch_customer_master.bulk_reconciliation."
	"reconcile_historical_sales_import"
)
TERMINAL_IMPORT_STATUSES = {"Success", "Partial Success", "Error", "Timed Out"}
INVOICE_BATCH_SIZE = 500
CUSTOMER_COMMIT_INTERVAL = 100


def get_company_go_live_date(company: str | None):
	"""Return the configured Company cutoff without depending on ch_erp15."""
	if not company or not frappe.db.has_column("Company", "custom_go_live_date"):
		return None
	value = frappe.get_cached_value("Company", company, "custom_go_live_date")
	return getdate(value) if value else None


def is_pre_go_live(company: str | None, posting_date: Any) -> bool:
	"""True only when a valid posting date is strictly before go-live."""
	if not company or not posting_date:
		return False
	go_live = get_company_go_live_date(company)
	if not go_live:
		return False
	try:
		return getdate(posting_date) < go_live
	except Exception:
		return False


def is_historic_sales_import(doc) -> bool:
	"""Identify a pre-go-live Sales Invoice currently submitted by Data Import."""
	flags = getattr(doc, "flags", None) or {}
	in_import = any(
		bool(getattr(frappe.flags, flag, False) or flags.get(flag))
		for flag in ("in_import", "in_data_import", "in_importer")
	)
	return bool(
		in_import
		and is_pre_go_live(doc.get("company"), doc.get("posting_date"))
	)


def after_background_job(method=None, kwargs=None, result=None, **_ignored):
	"""Queue reconciliation after a production Data Import worker has finished."""
	method_path = (
		f"{method.__module__}.{method.__qualname__}"
		if callable(method)
		else str(method or "")
	)
	if method_path != DATA_IMPORT_METHOD:
		return

	data_import = (kwargs or {}).get("data_import")
	if not data_import:
		return

	row = frappe.db.get_value(
		"Data Import",
		data_import,
		["reference_doctype", "status", "submit_after_import"],
		as_dict=True,
	)
	if (
		not row
		or row.reference_doctype != "Sales Invoice"
		or not row.submit_after_import
		or row.status not in TERMINAL_IMPORT_STATUSES
	):
		return

	_enqueue_reconciliation(data_import)


def on_data_import_change(doc, method=None):
	"""Cover successful synchronous/developer-mode Data Imports.

	Production imports are handled by :func:`after_background_job`, which also
	covers partial, failed, and timed-out imports containing successful rows.
	"""
	if (
		doc.reference_doctype == "Sales Invoice"
		and doc.submit_after_import
		and doc.status == "Success"
	):
		_enqueue_reconciliation(doc.name, enqueue_after_commit=True)


def _enqueue_reconciliation(data_import: str, *, enqueue_after_commit=False):
	if enqueue_after_commit:
		frappe.db.after_commit.add(
			lambda: _enqueue_reconciliation(data_import)
		)
		return
	try:
		frappe.enqueue(
			RECONCILE_METHOD,
			queue="long",
			timeout=21600,
			job_id=f"historic-sales-customer-reconcile::{data_import}",
			deduplicate=True,
			data_import=data_import,
		)
	except Exception:
		frappe.log_error(
			title=f"Historic sales reconciliation enqueue failed: {data_import}",
			message=frappe.get_traceback(),
		)


def _successful_invoice_names(
	data_import: str,
	page_size: int = INVOICE_BATCH_SIZE,
) -> Iterator[list[str]]:
	"""Yield deterministic, distinct invoice-name pages without loading the import."""
	cursor_index = -1
	cursor_name = ""
	while True:
		rows = frappe.db.sql(
			"""
				SELECT grouped.docname, grouped.first_log_index, grouped.first_log_name
				FROM (
					SELECT docname,
					       MIN(COALESCE(log_index, 0)) AS first_log_index,
					       MIN(name) AS first_log_name
					FROM `tabData Import Log`
					WHERE data_import = %(data_import)s
					  AND success = 1
					  AND docname IS NOT NULL
					  AND docname != ''
					GROUP BY docname
				) grouped
				WHERE grouped.first_log_index > %(cursor_index)s
				   OR (
					grouped.first_log_index = %(cursor_index)s
					AND grouped.first_log_name > %(cursor_name)s
				   )
				ORDER BY grouped.first_log_index ASC, grouped.first_log_name ASC
				LIMIT %(page_size)s
			""",
			{
				"data_import": data_import,
				"cursor_index": cursor_index,
				"cursor_name": cursor_name,
				"page_size": max(int(page_size or INVOICE_BATCH_SIZE), 1),
			},
			as_dict=True,
		)
		if not rows:
			return
		yield [row.docname for row in rows]
		cursor_index = rows[-1].first_log_index
		cursor_name = rows[-1].first_log_name


def _load_submitted_invoices(names: list[str]):
	if not names:
		return []
	return frappe.get_all(
		"Sales Invoice",
		filters={"name": ("in", names), "docstatus": 1},
		fields=[
			"name",
			"customer",
			"company",
			"posting_date",
			"set_warehouse",
			"owner",
			"is_return",
		],
		limit_page_length=0,
	)


def _visit_name(reference_doctype: str, reference_name: str) -> str:
	key = f"{reference_doctype}\0{reference_name}".encode()
	return f"customer-visit-{hashlib.sha256(key).hexdigest()[:40]}"


def _existing_visit_references(invoice_names: list[str]) -> set[str]:
	if not invoice_names:
		return set()
	return set(
		frappe.get_all(
			"CH Customer Store Visit",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_name": ("in", invoice_names),
			},
			pluck="reference_name",
			limit_page_length=0,
		)
	)


def _next_visit_indexes(customers: set[str]) -> dict[str, int]:
	if not customers:
		return {}
	rows = frappe.get_all(
		"CH Customer Store Visit",
		filters={
			"parent": ("in", list(customers)),
			"parenttype": "Customer",
			"parentfield": "ch_stores_visited",
		},
		fields=["parent", {"MAX": "idx", "as": "max_idx"}],
		group_by="parent",
		limit_page_length=0,
	)
	return {row.parent: int(row.max_idx or 0) for row in rows}


def _sync_existing_sales_visits(invoices: Iterable) -> None:
	"""Repair visits created by the old hook, including its load-date stamp."""
	invoice_names = [invoice.name for invoice in invoices]
	if not invoice_names:
		return
	frappe.db.sql(
		"""
		UPDATE `tabCH Customer Store Visit` visit
		INNER JOIN `tabSales Invoice` invoice
			ON invoice.name = visit.reference_name
			AND visit.reference_doctype = 'Sales Invoice'
		SET visit.visit_date = invoice.posting_date,
			visit.store = invoice.set_warehouse,
			visit.company = invoice.company,
			visit.visit_type = CASE
				WHEN invoice.is_return = 1 THEN 'Return'
				ELSE 'Purchase'
			END,
			visit.staff = invoice.owner,
			visit.parent = invoice.customer,
			visit.parentfield = 'ch_stores_visited',
			visit.parenttype = 'Customer'
		WHERE invoice.name IN %(invoice_names)s
		""",
		{"invoice_names": tuple(invoice_names)},
	)


def _insert_missing_sales_visits(invoices: Iterable) -> int:
	invoices = list(invoices)
	_sync_existing_sales_visits(invoices)
	existing = _existing_visit_references([row.name for row in invoices])
	customers = {row.customer for row in invoices if row.customer}
	next_indexes = _next_visit_indexes(customers)
	now = now_datetime()
	fields = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"visit_date",
		"store",
		"company",
		"visit_type",
		"reference_doctype",
		"reference_name",
		"staff",
		"parent",
		"parentfield",
		"parenttype",
	]
	values = []

	for invoice in invoices:
		if not invoice.customer or invoice.name in existing:
			continue
		next_indexes[invoice.customer] = next_indexes.get(invoice.customer, 0) + 1
		owner = invoice.owner or frappe.session.user
		values.append(
			(
				_visit_name("Sales Invoice", invoice.name),
				now,
				now,
				owner,
				owner,
				0,
				next_indexes[invoice.customer],
				invoice.posting_date,
				invoice.set_warehouse,
				invoice.company,
				"Return" if invoice.is_return else "Purchase",
				"Sales Invoice",
				invoice.name,
				owner,
				invoice.customer,
				"ch_stores_visited",
				"Customer",
			)
		)

	if values:
		frappe.db.bulk_insert(
			"CH Customer Store Visit",
			fields,
			values,
			ignore_duplicates=True,
			chunk_size=INVOICE_BATCH_SIZE,
		)
	return len(values)


def reconcile_historical_sales_import(data_import: str) -> dict[str, int]:
	"""Reconcile successful pre-go-live invoices once per distinct customer."""
	from ch_item_master.ch_customer_master.hooks import (
		_refresh_last_visit_summary,
		_update_activity_summary,
	)

	affected_customers: set[str] = set()
	successful_logs = 0
	historic_invoices = 0
	visits_inserted = 0

	for name_batch in _successful_invoice_names(data_import):
		successful_logs += len(name_batch)
		invoices = [
			invoice
			for invoice in _load_submitted_invoices(name_batch)
			if invoice.customer
			and is_pre_go_live(invoice.company, invoice.posting_date)
		]
		if not invoices:
			continue
		historic_invoices += len(invoices)
		affected_customers.update(invoice.customer for invoice in invoices)
		visits_inserted += _insert_missing_sales_visits(invoices)
		frappe.db.commit()

	for index, customer in enumerate(sorted(affected_customers), start=1):
		_refresh_last_visit_summary(customer)
		_update_activity_summary(customer)
		if index % CUSTOMER_COMMIT_INTERVAL == 0:
			frappe.db.commit()

	frappe.db.commit()
	return {
		"successful_logs": successful_logs,
		"historic_invoices": historic_invoices,
		"visits_inserted": visits_inserted,
		"customers_reconciled": len(affected_customers),
	}


def install_customer_activity_indexes():
	"""Install the non-unique lookup indexes used by the idempotent flow."""
	indexes = (
		(
			"CH Customer Store Visit",
			["reference_doctype", "reference_name"],
			"idx_customer_visit_reference",
		),
		(
			"Sales Invoice",
			["customer", "docstatus"],
			"idx_si_customer_docstatus",
		),
		(
			"Data Import Log",
			["data_import(100)", "success", "docname(100)"],
			"idx_data_import_log_success",
		),
	)
	for doctype, fields, index_name in indexes:
		if frappe.db.table_exists(doctype):
			frappe.db.add_index(doctype, fields, index_name=index_name)
