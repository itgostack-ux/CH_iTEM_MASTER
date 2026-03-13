# Copyright (c) 2026, GoStack and contributors
# CH Scheme Receivable Aging Report
#
# Shows outstanding amounts from banks/brands (bank offer reimbursements,
# brand co-op claims, EMI subvention) bucketed by days overdue.
# Also generates dunning notices via send_dunning_notice().

import frappe
from frappe import _
from frappe.utils import flt, date_diff, nowdate


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": _("Receivable"), "fieldname": "name", "fieldtype": "Link",
         "options": "CH Scheme Receivable", "width": 160},
        {"label": _("Scheme Type"), "fieldname": "scheme_type", "fieldtype": "Data", "width": 120},
        {"label": _("Party"), "fieldname": "party", "fieldtype": "Data", "width": 140},
        {"label": _("Claim Date"), "fieldname": "claim_date", "fieldtype": "Date", "width": 100},
        {"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 100},
        {"label": _("Claim Amount"), "fieldname": "claim_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Received"), "fieldname": "received_amount", "fieldtype": "Currency", "width": 110},
        {"label": _("Written Off"), "fieldname": "written_off_amount", "fieldtype": "Currency", "width": 110},
        {"label": _("Outstanding"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Days Overdue"), "fieldname": "days_overdue", "fieldtype": "Int", "width": 100},
        {"label": _("0-30 Days"), "fieldname": "bucket_0_30", "fieldtype": "Currency", "width": 110},
        {"label": _("31-60 Days"), "fieldname": "bucket_31_60", "fieldtype": "Currency", "width": 110},
        {"label": _("61-90 Days"), "fieldname": "bucket_61_90", "fieldtype": "Currency", "width": 110},
        {"label": _("90+ Days"), "fieldname": "bucket_90plus", "fieldtype": "Currency", "width": 110},
        {"label": _("Last Dunning"), "fieldname": "last_dunning_date", "fieldtype": "Date", "width": 110},
    ]


def get_data(filters):
    conditions = _build_conditions(filters)

    receivables = frappe.db.sql("""
        SELECT
            r.name,
            r.scheme_type,
            r.party,
            r.claim_date,
            r.due_date,
            r.claim_amount,
            r.received_amount,
            r.written_off_amount,
            r.outstanding_amount,
            r.status,
            r.last_dunning_date
        FROM `tabCH Scheme Receivable` r
        WHERE r.docstatus = 1
          AND r.status NOT IN ('Settled', 'Cancelled', 'Written Off')
          AND r.outstanding_amount > 0
          {conditions}
        ORDER BY r.due_date ASC
    """.format(conditions=conditions), filters, as_dict=True)

    today = nowdate()
    data = []

    for row in receivables:
        due_date = str(row.due_date) if row.due_date else str(row.claim_date or today)
        days_overdue = max(0, date_diff(today, due_date))
        outstanding = flt(row.outstanding_amount)

        bucket_0_30 = outstanding if days_overdue <= 30 else 0
        bucket_31_60 = outstanding if 31 <= days_overdue <= 60 else 0
        bucket_61_90 = outstanding if 61 <= days_overdue <= 90 else 0
        bucket_90plus = outstanding if days_overdue > 90 else 0

        data.append({
            "name": row.name,
            "scheme_type": row.scheme_type or "",
            "party": row.party or "",
            "claim_date": row.claim_date,
            "due_date": row.due_date,
            "claim_amount": flt(row.claim_amount),
            "received_amount": flt(row.received_amount),
            "written_off_amount": flt(row.written_off_amount),
            "outstanding_amount": outstanding,
            "days_overdue": days_overdue,
            "bucket_0_30": bucket_0_30,
            "bucket_31_60": bucket_31_60,
            "bucket_61_90": bucket_61_90,
            "bucket_90plus": bucket_90plus,
            "last_dunning_date": row.last_dunning_date,
        })

    return data


def _build_conditions(filters):
    conditions = []
    if filters.get("company"):
        conditions.append("r.company = %(company)s")
    if filters.get("scheme_type"):
        conditions.append("r.scheme_type = %(scheme_type)s")
    if filters.get("party"):
        conditions.append("r.party LIKE %(party)s")
        filters["party"] = f"%{filters['party']}%"
    if filters.get("status"):
        conditions.append("r.status = %(status)s")
    if filters.get("overdue_only"):
        conditions.append("r.due_date < CURDATE()")
    return ("AND " + " AND ".join(conditions)) if conditions else ""


def get_filters():
    return [
        {
            "fieldname": "company",
            "label": _("Company"),
            "fieldtype": "Link",
            "options": "Company",
        },
        {
            "fieldname": "scheme_type",
            "label": _("Scheme Type"),
            "fieldtype": "Select",
            "options": "\nBank Offer\nBrand Co-op\nBrand Cashback\nEMI Subvention\nOther",
        },
        {
            "fieldname": "party",
            "label": _("Party"),
            "fieldtype": "Data",
        },
        {
            "fieldname": "overdue_only",
            "label": _("Overdue Only"),
            "fieldtype": "Check",
            "default": 1,
        },
    ]
