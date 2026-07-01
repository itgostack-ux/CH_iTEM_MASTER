# Copyright (c) 2026, GoStack and contributors
# CH POS Override Audit — SAP IS-Retail "POS Audit Cockpit" / Oracle Retail Xstore
# "Price Override Report" parity. Groups override logs by date, store, cashier,
# override type; exposes margin leakage and surfaces repeat offenders.

import frappe
from frappe import _
from frappe.utils import flt, cint, getdate, add_days, nowdate

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def execute(filters=None):
	filters = frappe._dict(filters or {})
	_apply_defaults(filters)

	data = _fetch_rows(filters)
	columns = _get_columns()
	summary = _build_summary(data, filters)
	chart = _build_chart(data)

	return columns, data, None, chart, summary


# ─────────────────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────────────────

def _apply_defaults(filters):
	if not filters.get("from_date"):
		filters.from_date = add_days(nowdate(), -30)
	if not filters.get("to_date"):
		filters.to_date = nowdate()


def _fetch_rows(filters):
	conditions = ["l.posting_date BETWEEN %(from_date)s AND %(to_date)s"]
	params = {
		"from_date": getdate(filters.from_date),
		"to_date": getdate(filters.to_date),
	}

	if filters.get("company"):
		conditions.append("l.company = %(company)s")
		params["company"] = filters.company

	if filters.get("store_warehouse"):
		conditions.append("l.store_warehouse = %(store_warehouse)s")
		params["store_warehouse"] = filters.store_warehouse

	if filters.get("pos_user"):
		conditions.append("l.pos_user = %(pos_user)s")
		params["pos_user"] = filters.pos_user

	if filters.get("customer"):
		conditions.append("l.customer = %(customer)s")
		params["customer"] = filters.customer

	if filters.get("override_type"):
		conditions.append("l.override_type = %(override_type)s")
		params["override_type"] = filters.override_type

	if filters.get("only_unapproved"):
		conditions.append("IFNULL(l.approved_by_manager, 0) = 0")

	# Tier 4: fail-closed scope on POS Override Log's store_warehouse and
	# pos_profile. Enterprise parity with SAP IS-Retail POS auth object
	# W_POS_STORE and Oracle Xstore's Register auth model.
	scope = scope_where_clause(
		warehouse_field="l.store_warehouse",
		pos_profile_field="l.pos_profile",
	)
	if scope is not None:
		conditions.append(scope)

	where = " AND ".join(conditions)

	rows = frappe.db.sql(
		f"""
		SELECT
			l.name,
			l.posting_date,
			l.override_at,
			l.company,
			l.store_warehouse,
			l.pos_user,
			l.customer,
			l.pos_invoice,
			l.item_code,
			l.item_name,
			l.override_type,
			l.original_price,
			l.applied_price,
			l.discount_percent,
			l.discount_amount,
			l.approved_by_manager,
			l.manager_user,
			l.override_reason,
			l.currency
		FROM `tabCH POS Override Log` l
		WHERE {where}
		ORDER BY l.override_at DESC
		""",
		params,
		as_dict=True,
	)

	for r in rows:
		r["approval_status"] = "Approved" if cint(r.get("approved_by_manager")) else "Unapproved"

	return rows


# ─────────────────────────────────────────────────────────────────────────────
# Columns
# ─────────────────────────────────────────────────────────────────────────────

def _get_columns():
	return [
		{"fieldname": "name", "label": _("Log"), "fieldtype": "Link", "options": "CH POS Override Log", "width": 140},
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 160},
		{"fieldname": "store_warehouse", "label": _("Store"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "pos_user", "label": _("Cashier"), "fieldtype": "Link", "options": "User", "width": 180},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 160},
		{"fieldname": "pos_invoice", "label": _("Invoice"), "fieldtype": "Link", "options": "POS Invoice", "width": 130},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
		{"fieldname": "override_type", "label": _("Type"), "fieldtype": "Data", "width": 130},
		{"fieldname": "original_price", "label": _("Original"), "fieldtype": "Currency", "options": "currency", "width": 110},
		{"fieldname": "applied_price", "label": _("Applied"), "fieldtype": "Currency", "options": "currency", "width": 110},
		{"fieldname": "discount_percent", "label": _("Discount %"), "fieldtype": "Percent", "width": 100},
		{"fieldname": "discount_amount", "label": _("Leakage"), "fieldtype": "Currency", "options": "currency", "width": 110},
		{"fieldname": "approval_status", "label": _("Approval"), "fieldtype": "Data", "width": 110},
		{"fieldname": "manager_user", "label": _("Manager"), "fieldtype": "Link", "options": "User", "width": 160},
		{"fieldname": "override_reason", "label": _("Reason"), "fieldtype": "Data", "width": 240},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "width": 80},
	]


# ─────────────────────────────────────────────────────────────────────────────
# Summary cards
# ─────────────────────────────────────────────────────────────────────────────

def _build_summary(rows, filters):
	total = len(rows)
	unapproved = sum(1 for r in rows if not cint(r.get("approved_by_manager")))
	leakage = sum(flt(r.get("discount_amount")) for r in rows)

	# Top cashier (count of overrides) — Oracle Xstore "Operator Performance"
	cashier_counts = {}
	for r in rows:
		u = r.get("pos_user") or "—"
		cashier_counts[u] = cashier_counts.get(u, 0) + 1
	top_cashier = max(cashier_counts.items(), key=lambda x: x[1]) if cashier_counts else (None, 0)

	# Currency for the leakage card — first non-empty wins (mixed-company reports
	# are rare; the field's options="currency" handles row-level formatting).
	leakage_ccy = next((r.get("currency") for r in rows if r.get("currency")), None)

	pct_unapproved = (unapproved * 100.0 / total) if total else 0

	return [
		{"label": _("Total Overrides"), "value": total, "indicator": "Blue"},
		{
			"label": _("Total Margin Leakage"),
			"value": leakage,
			"datatype": "Currency",
			"currency": leakage_ccy,
			"indicator": "Orange" if leakage > 0 else "Green",
		},
		{
			"label": _("% Unapproved"),
			"value": round(pct_unapproved, 1),
			"datatype": "Percent",
			"indicator": "Red" if pct_unapproved > 10 else ("Orange" if pct_unapproved > 0 else "Green"),
		},
		{
			"label": _("Top Cashier"),
			"value": f"{top_cashier[0] or '—'} ({top_cashier[1]})",
			"indicator": "Grey",
		},
		{
			"label": _("Date Range"),
			"value": f"{filters.from_date} → {filters.to_date}",
			"indicator": "Grey",
		},
	]


# ─────────────────────────────────────────────────────────────────────────────
# Chart — donut by override type
# ─────────────────────────────────────────────────────────────────────────────

def _build_chart(rows):
	if not rows:
		return None

	buckets = {}
	for r in rows:
		t = r.get("override_type") or "Unknown"
		buckets[t] = buckets.get(t, 0) + 1

	labels = list(buckets.keys())
	values = list(buckets.values())

	return {
		"data": {
			"labels": labels,
			"datasets": [{"name": _("Overrides"), "values": values}],
		},
		"type": "donut",
		"height": 260,
		"colors": ["#5e64ff", "#ff5858", "#ffa00a", "#28a745", "#7575ff"],
	}
