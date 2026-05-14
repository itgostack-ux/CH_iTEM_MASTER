"""
Thin enqueue trampolines for heavy Sales Invoice / POS Invoice on_submit hooks.

Why: at 100 concurrent POS users, post-submit work (customer activity
recompute, supplier scheme matching, scheme receivable creation) measurably
slows down the request thread without affecting GL/SLE correctness. We move
them to background queues so the POS request can return immediately after the
submit commits.

Pattern: each wrapper does the cheapest possible work (capture doc.name and
doc.doctype) then frappe.enqueue's the real handler with enqueue_after_commit
so the worker reads a fully-committed document.

Original handlers remain importable and can still be invoked synchronously
(tests, manual recompute, scheduled jobs) — these wrappers only change the
hook entry-points.
"""

import frappe


# ── module-level fully-qualified handler paths ──────────────────────────
_CUSTOMER_HOOK = "ch_item_master.ch_customer_master.hooks.on_sales_invoice_submit"
_SCHEME_RECEIVABLE_HOOK = (
    "ch_item_master.ch_item_master.doctype.ch_scheme_receivable."
    "ch_scheme_receivable.create_from_pos_invoice"
)
_SUPPLIER_SCHEME_HOOK = "ch_item_master.supplier_scheme.engine.process_invoice_items"


def _enqueue(method_path, doc, *, queue="default", timeout=600):
    """Enqueue a doc-event handler to run after the current transaction commits.

    The worker re-fetches the doc by name so we never carry stale state across
    the boundary.
    """
    try:
        frappe.enqueue(
            "ch_item_master.async_dispatch._run_doc_handler",
            queue=queue,
            timeout=timeout,
            enqueue_after_commit=True,
            method_path=method_path,
            doctype=doc.doctype,
            docname=doc.name,
            job_name=f"{method_path}::{doc.name}",
        )
    except Exception:
        # Never block the submit on enqueue failure — fall back to inline.
        frappe.log_error(
            title=f"Async enqueue failed: {method_path}",
            message=frappe.get_traceback(),
        )
        try:
            _run_doc_handler(method_path, doc.doctype, doc.name)
        except Exception:
            frappe.log_error(
                title=f"Inline fallback failed: {method_path}",
                message=frappe.get_traceback(),
            )


def _run_doc_handler(method_path, doctype, docname):
    """Worker entry-point: re-fetch the doc and invoke the original handler."""
    if not frappe.db.exists(doctype, docname):
        return
    doc = frappe.get_doc(doctype, docname)
    handler = frappe.get_attr(method_path)
    handler(doc, method="on_submit")


# ── on_submit wrappers ──────────────────────────────────────────────────

def customer_activity_after_submit(doc, method=None):
    """Recompute customer activity summary in background (queue=default)."""
    if doc.docstatus != 1:
        return
    _enqueue(_CUSTOMER_HOOK, doc, queue="default", timeout=300)


def scheme_receivable_after_submit(doc, method=None):
    """Create CH Scheme Receivable rows in background (queue=default)."""
    if doc.docstatus != 1:
        return
    _enqueue(_SCHEME_RECEIVABLE_HOOK, doc, queue="default", timeout=300)


def supplier_scheme_after_submit(doc, method=None):
    """Run supplier-scheme achievement matching in background (queue=long)."""
    if doc.docstatus != 1:
        return
    _enqueue(_SUPPLIER_SCHEME_HOOK, doc, queue="long", timeout=900)
