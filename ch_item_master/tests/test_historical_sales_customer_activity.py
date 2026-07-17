"""Regression tests for bulk-safe historic Sales Invoice customer activity."""

from unittest.mock import call, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate

from ch_item_master import async_dispatch
from ch_item_master.ch_customer_master import bulk_reconciliation, hooks


def _invoice(name="INV-1", customer="CUST-1", posting_date="2026-01-01"):
	return frappe._dict(
		name=name,
		doctype="Sales Invoice",
		docstatus=1,
		customer=customer,
		company="Test Company",
		posting_date=posting_date,
		set_warehouse="Stores - TC",
		owner="Administrator",
		is_return=0,
		flags=frappe._dict(),
	)


class TestHistoricSalesCustomerActivity(IntegrationTestCase):
	def test_go_live_boundary_is_strict(self):
		with patch.object(
			bulk_reconciliation,
			"get_company_go_live_date",
			return_value=getdate("2026-07-12"),
		):
			self.assertTrue(
				bulk_reconciliation.is_pre_go_live(
					"Test Company", "2026-07-11"
				)
			)
			self.assertFalse(
				bulk_reconciliation.is_pre_go_live(
					"Test Company", "2026-07-12"
				)
			)
			self.assertFalse(
				bulk_reconciliation.is_pre_go_live(
					"Test Company", "2026-07-13"
				)
			)

	def test_historic_import_skips_only_customer_activity_job(self):
		doc = _invoice()
		with (
			patch.object(
				bulk_reconciliation,
				"is_historic_sales_import",
				return_value=True,
			),
			patch.object(async_dispatch, "_enqueue") as enqueue,
		):
			async_dispatch.customer_activity_after_submit(doc)
			enqueue.assert_not_called()

			async_dispatch.scheme_receivable_after_submit(doc)
			async_dispatch.supplier_scheme_after_submit(doc)

		self.assertEqual(enqueue.call_count, 2)
		self.assertEqual(
			enqueue.call_args_list[0].args[0],
			async_dispatch._SCHEME_RECEIVABLE_HOOK,
		)
		self.assertEqual(
			enqueue.call_args_list[1].args[0],
			async_dispatch._SUPPLIER_SCHEME_HOOK,
		)

	def test_current_import_keeps_customer_activity_job(self):
		doc = _invoice(posting_date="2026-07-12")
		with (
			patch.object(
				bulk_reconciliation,
				"is_historic_sales_import",
				return_value=False,
			),
			patch.object(async_dispatch, "_enqueue") as enqueue,
		):
			async_dispatch.customer_activity_after_submit(doc)

		enqueue.assert_called_once_with(
			async_dispatch._CUSTOMER_HOOK,
			doc,
			queue="default",
			timeout=300,
		)

	def test_submit_uses_invoice_posting_date(self):
		doc = _invoice(posting_date="2025-12-31")
		with (
			patch.object(hooks, "_log_store_visit") as log_visit,
			patch.object(hooks, "_update_activity_summary") as update_summary,
		):
			hooks.on_sales_invoice_submit(doc)

		self.assertEqual(log_visit.call_args.kwargs["visit_date"], "2025-12-31")
		update_summary.assert_called_once_with("CUST-1")

	def test_cancelled_invoice_cannot_run_submit_handler(self):
		doc = _invoice()
		doc.docstatus = 2
		with (
			patch.object(hooks, "_log_store_visit") as log_visit,
			patch.object(hooks, "_update_activity_summary") as update_summary,
		):
			hooks.on_sales_invoice_submit(doc)
		log_visit.assert_not_called()
		update_summary.assert_not_called()

	def test_visit_reference_is_idempotent(self):
		with (
			patch.object(frappe.db, "exists", return_value="EXISTING-VISIT"),
			patch.object(frappe.db, "sql") as sql,
		):
			created = hooks._log_store_visit(
				customer="CUST-1",
				company="Test Company",
				visit_type="Purchase",
				reference_doctype="Sales Invoice",
				reference_name="INV-1",
				visit_date="2026-01-01",
			)

		self.assertFalse(created)
		sql.assert_not_called()

	def test_real_visit_insert_is_idempotent_and_keeps_posting_date(self):
		customer = frappe.db.get_value("Customer", {}, "name")
		if not customer:
			self.skipTest("No Customer available for child-row insert")

		reference_name = f"HIST-ACTIVITY-{frappe.generate_hash(length=8)}"
		visit_date = "2025-12-30"
		created = hooks._log_store_visit(
			customer=customer,
			company=frappe.db.get_value("Company", {}, "name"),
			visit_type="Purchase",
			reference_doctype="Sales Invoice",
			reference_name=reference_name,
			visit_date=visit_date,
			staff="Administrator",
		)
		created_again = hooks._log_store_visit(
			customer=customer,
			company=frappe.db.get_value("Company", {}, "name"),
			visit_type="Purchase",
			reference_doctype="Sales Invoice",
			reference_name=reference_name,
			visit_date=visit_date,
			staff="Administrator",
		)
		rows = frappe.get_all(
			"CH Customer Store Visit",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_name": reference_name,
			},
			pluck="visit_date",
		)

		self.assertTrue(created)
		self.assertFalse(created_again)
		self.assertEqual(rows, [getdate(visit_date)])

	def test_bulk_visit_insert_is_idempotent_and_keeps_posting_date(self):
		customer = frappe.db.get_value("Customer", {}, "name")
		if not customer:
			self.skipTest("No Customer available for bulk child-row insert")

		reference_name = f"HIST-BULK-{frappe.generate_hash(length=8)}"
		visit_date = "2025-12-29"
		invoice = _invoice(
			name=reference_name,
			customer=customer,
			posting_date=visit_date,
		)
		invoice.company = frappe.db.get_value("Company", {}, "name")

		inserted = bulk_reconciliation._insert_missing_sales_visits([invoice])
		inserted_again = bulk_reconciliation._insert_missing_sales_visits([invoice])
		rows = frappe.get_all(
			"CH Customer Store Visit",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_name": reference_name,
			},
			pluck="visit_date",
		)

		self.assertEqual(inserted, 1)
		self.assertEqual(inserted_again, 0)
		self.assertEqual(rows, [getdate(visit_date)])

	def test_cancel_deletes_visit_and_reconciles_customer(self):
		doc = _invoice()
		doc.docstatus = 2
		with (
			patch.object(frappe.db, "delete") as delete,
			patch.object(hooks, "_refresh_last_visit_summary") as refresh,
			patch.object(hooks, "_update_activity_summary") as update,
		):
			hooks.on_sales_invoice_cancel(doc)

		delete.assert_called_once_with(
			"CH Customer Store Visit",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": "INV-1",
			},
		)
		refresh.assert_called_once_with("CUST-1")
		update.assert_called_once_with("CUST-1")

	def test_bulk_reconciliation_updates_each_customer_once(self):
		invoices = [
			_invoice(name="INV-1", customer="CUST-1"),
			_invoice(name="INV-2", customer="CUST-1"),
			_invoice(name="INV-3", customer="CUST-2"),
		]
		with (
			patch.object(
				bulk_reconciliation,
				"_successful_invoice_names",
				return_value=["INV-1", "INV-2", "INV-3"],
			),
			patch.object(
				bulk_reconciliation,
				"_load_submitted_invoices",
				return_value=invoices,
			),
			patch.object(
				bulk_reconciliation,
				"is_pre_go_live",
				return_value=True,
			),
			patch.object(
				bulk_reconciliation,
				"_insert_missing_sales_visits",
				return_value=3,
			),
			patch.object(hooks, "_refresh_last_visit_summary") as refresh,
			patch.object(hooks, "_update_activity_summary") as update,
			patch.object(frappe.db, "commit"),
		):
			result = bulk_reconciliation.reconcile_historical_sales_import(
				"Sales Invoice Import 1"
			)

		self.assertEqual(
			refresh.call_args_list,
			[call("CUST-1"), call("CUST-2")],
		)
		self.assertEqual(
			update.call_args_list,
			[call("CUST-1"), call("CUST-2")],
		)
		self.assertEqual(result["historic_invoices"], 3)
		self.assertEqual(result["customers_reconciled"], 2)

	def test_data_import_after_job_uses_long_reconciliation(self):
		row = frappe._dict(
			reference_doctype="Sales Invoice",
			status="Partial Success",
			submit_after_import=1,
		)
		with (
			patch.object(frappe.db, "get_value", return_value=row),
			patch.object(
				bulk_reconciliation, "_enqueue_reconciliation"
			) as enqueue,
		):
			bulk_reconciliation.after_background_job(
				method=bulk_reconciliation.DATA_IMPORT_METHOD,
				kwargs={"data_import": "Sales Invoice Import 1"},
			)

		enqueue.assert_called_once_with("Sales Invoice Import 1")

	def test_reconciliation_queue_failure_does_not_fail_import(self):
		with (
			patch.object(frappe, "enqueue", side_effect=RuntimeError("queue down")),
			patch.object(frappe, "log_error") as log_error,
		):
			bulk_reconciliation._enqueue_reconciliation(
				"Sales Invoice Import 1"
			)

		log_error.assert_called_once()
