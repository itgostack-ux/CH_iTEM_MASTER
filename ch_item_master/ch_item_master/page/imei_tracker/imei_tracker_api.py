# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
IMEI Tracker — backend API.

A consolidated lifecycle tracker for serialized inventory:
  * Real IMEIs (mobile phones, 15-digit manufacturer IDs)
  * System-generated barcode serials (laptops, accessories, anything else)

Powered by the existing CH Serial Lifecycle (11-state machine) and
buyback.serial_no_utils.get_imei_history (canonical history aggregator).

All endpoints honor the standard hub cascade filter
(company / city / zone / store / from_date / to_date) and apply
CH User Scope (store-level RBAC) via ch_item_master.security helpers.
"""

import csv
import io
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, now_datetime, nowdate, today
from ch_item_master.config import (
    get_int_setting,
    get_list_setting,
    has_role_setting,
    is_privileged_user,
    require_role_setting,
)


# ── Status bucket grouping ────────────────────────────────────────────────────
# Maps the 11-state CH Serial Lifecycle into 6 user-facing buckets.
STATUS_BUCKETS = {
    "in_stock":     ["Received", "In Stock", "Displayed", "Refurbished"],
    "sold":         ["Sold", "Delivered"],
    "returned":     ["Returned", "Customer Return"],
    "in_service":   ["In Service", "Repaired"],
    "bought_back":  ["Buyback"],
    "out_of_pool":  ["Scrapped", "Lost"],
}

BUCKET_LABELS = {
    "in_stock":     "In Stock",
    "sold":         "Sold",
    "returned":     "Returned",
    "in_service":   "In Service",
    "bought_back":  "Bought Back",
    "out_of_pool":  "Out of Pool",
}


@frappe.whitelist()
def get_ui_capabilities():
    return {
        "can_bulk_update": has_role_setting(
            "bulk_imei_roles",
            defaults=("System Manager", "Stock Manager", "CH Master Manager"),
        ),
        "unsold_stale_days": get_int_setting("imei_unsold_stale_days", 90),
        "service_stale_days": get_int_setting("imei_service_stale_days", 30),
    }


# ── Serial-kind tri-state classification ──────────────────────────────────────
# SAP MARC-SERNP / Oracle mtl_system_items.serial_number_control_code /
# Microsoft D365 Tracking Dimension Group equivalent.
#
# Source-of-truth priority (highest → lowest):
#   1. CH Serial Lifecycle.ch_serial_kind  (mirrored on PR submit)
#   2. Serial No.ch_serial_kind            (stamped on PR submit)
#   3. Item.ch_serial_kind                 (authoritative master)
#   4. Legacy Serial No.ch_is_imei boolean (pre-v5 data — IMEI / Barcode only)
#
# Falls back to 'Barcode' so legacy serialised items always classify.
SERIAL_KIND_VALUES = ("IMEI", "Barcode", "UOM")

SERIAL_KIND_EXPR = (
    "COALESCE("
    "NULLIF(lc.ch_serial_kind,''), "
    "NULLIF(sn.ch_serial_kind,''), "
    "NULLIF(itm.ch_serial_kind,''), "
    "CASE WHEN IFNULL(sn.ch_is_imei,0)=1 THEN 'IMEI' ELSE 'Barcode' END"
    ")"
)


# ── Filter normalization ──────────────────────────────────────────────────────
def _norm_filters(filters):
    """Coerce JSON / dict / None to a dict with consistent keys."""
    if not filters:
        return {}
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except Exception:
            return {}
    return {
        "company":       filters.get("company")    or None,
        "city":          filters.get("city")       or None,
        "zone":          filters.get("zone")       or None,
        "store":         filters.get("store")      or None,
        "from_date":     filters.get("from_date")  or None,
        "to_date":       filters.get("to_date")    or None,
        # New tri-state classification filter (preferred).
        # 'kind' may be one of: '', 'IMEI', 'Barcode', 'UOM'.
        "kind":          (filters.get("kind") or "").strip(),
        # Legacy binary inputs — still accepted for backwards compatibility.
        "imei_only":     cint(filters.get("imei_only")     or 0),
        "non_imei_only": cint(filters.get("non_imei_only") or 0),
        "status_bucket": filters.get("status_bucket") or None,
        "search":        (filters.get("search") or "").strip(),
        "item_code":     filters.get("item_code") or None,
        "brand":         filters.get("brand") or None,
        "aging_bucket":  filters.get("aging_bucket") or None,
        # Pagination (server-side). `start` is the 0-based row offset,
        # `page_length` the rows-per-page (legacy alias: `limit`).
        "start":         max(cint(filters.get("start") or 0), 0),
        "page_length":   cint(filters.get("page_length") or filters.get("limit") or 0),
    }


def _build_where(f, alias_lc="lc", alias_sn="sn"):
    """Build the SQL WHERE fragment + parameter dict shared by all queries.

    Joins are assumed to be:
      `tabCH Serial Lifecycle` lc
      LEFT JOIN `tabSerial No`  sn ON sn.name = lc.serial_no
      LEFT JOIN `tabWarehouse`  wh ON wh.name = lc.current_warehouse
    """
    where = ["1=1"]
    params = {}

    if f.get("company"):
        # Backward-compatible filter: some historical lifecycle rows were
        # created before current_company was stamped correctly. Fall back to
        # Warehouse.company so those rows remain visible in company-scoped views.
        where.append(
            f"COALESCE({alias_lc}.current_company, wh.company) = %(company)s"
        )
        params["company"] = f["company"]

    if f.get("store"):
        # Resolve CH Store → Warehouse so the filter works whether the caller
        # passes a CH Store name or a raw Warehouse name (cascade picker passes Store).
        store_wh = frappe.db.get_value("CH Store", f["store"], "warehouse") or f["store"]
        where.append(f"{alias_lc}.current_warehouse = %(store_wh)s")
        params["store_wh"] = store_wh
    elif f.get("zone"):
        where.append("wh.ch_zone = %(zone)s")
        params["zone"] = f["zone"]
    elif f.get("city"):
        where.append("wh.ch_city = %(city)s")
        params["city"] = f["city"]
    elif f.get("_allowed_warehouses") is not None:
        # Scope intersection result (set by get_imei_tracker_data). Caller has no
        # explicit store/zone/city, so constrain to the user's allowed warehouses
        # (or — for unscoped users — to all warehouses of the requested company).
        wlist = f["_allowed_warehouses"]
        if not wlist:
            where.append("1=0")
        else:
            placeholders = ", ".join([f"%(wh_{i})s" for i in range(len(wlist))])
            where.append(f"{alias_lc}.current_warehouse IN ({placeholders})")
            for i, w in enumerate(wlist):
                params[f"wh_{i}"] = w

    if f.get("from_date"):
        where.append(f"({alias_lc}.purchase_date >= %(from_date)s OR {alias_lc}.modified >= %(from_date)s)")
        params["from_date"] = f["from_date"]
    if f.get("to_date"):
        where.append(f"({alias_lc}.purchase_date <= %(to_date)s OR {alias_lc}.modified <= %(to_date)s)")
        params["to_date"] = f["to_date"]

    # ── Serial-kind tri-state filter ─────────────────────────────────────
    # Preferred input: f['kind'] in ('IMEI', 'Barcode', 'UOM').
    # Legacy inputs imei_only / non_imei_only still honoured:
    #   imei_only=1     → kind = IMEI
    #   non_imei_only=1 → kind IN (Barcode, UOM)
    kind = (f.get("kind") or "").strip()
    if kind in SERIAL_KIND_VALUES:
        where.append(f"{SERIAL_KIND_EXPR} = %(serial_kind)s")
        params["serial_kind"] = kind
    elif f.get("imei_only"):
        where.append(f"{SERIAL_KIND_EXPR} = 'IMEI'")
    elif f.get("non_imei_only"):
        where.append(f"{SERIAL_KIND_EXPR} IN ('Barcode', 'UOM')")

    if f.get("status_bucket"):
        statuses = STATUS_BUCKETS.get(f["status_bucket"], [])
        if statuses:
            placeholders = ", ".join([f"%(s_{i})s" for i in range(len(statuses))])
            where.append(f"{alias_lc}.lifecycle_status IN ({placeholders})")
            for i, s in enumerate(statuses):
                params[f"s_{i}"] = s

    if f.get("item_code"):
        where.append(f"{alias_lc}.item_code = %(item_code)s")
        params["item_code"] = f["item_code"]

    if f.get("brand"):
        where.append("itm.brand = %(brand)s")
        params["brand"] = f["brand"]

    if f.get("search"):
        # Match IMEI, IMEI 2, serial_no, or item_code (partial / last-N digit)
        where.append(
            f"({alias_lc}.serial_no LIKE %(search)s "
            f"OR {alias_lc}.imei_number LIKE %(search)s "
            f"OR {alias_lc}.imei_number_2 LIKE %(search)s "
            f"OR {alias_lc}.item_code LIKE %(search)s)"
        )
        params["search"] = f"%{f['search']}%"

    if f.get("aging_bucket"):
        # Aging buckets — based on (today - last_status_change)
        # We approximate last_status_change via lc.modified for performance.
        if f["aging_bucket"] == "unsold_90":
            where.append(f"{alias_lc}.lifecycle_status IN ('Received','In Stock','Displayed') "
                         f"AND DATEDIFF(%(today)s, {alias_lc}.purchase_date) > %(unsold_stale_days)s")
            params["today"] = today()
            params["unsold_stale_days"] = get_int_setting("imei_unsold_stale_days", 90)
        elif f["aging_bucket"] == "in_service_30":
            where.append(f"{alias_lc}.lifecycle_status IN ('In Service','Repaired') "
                         f"AND DATEDIFF(%(today)s, {alias_lc}.modified) > %(service_stale_days)s")
            params["today"] = today()
            params["service_stale_days"] = get_int_setting("imei_service_stale_days", 30)

    return " AND ".join(where), params


def _join_clause():
    """Standard JOIN block used by list / kpi / export queries."""
    return (
        " FROM `tabCH Serial Lifecycle` lc "
        " LEFT JOIN `tabSerial No` sn ON sn.name = lc.serial_no "
        " LEFT JOIN `tabWarehouse` wh ON wh.name = lc.current_warehouse "
        " LEFT JOIN `tabItem` itm     ON itm.name = lc.item_code "
    )


# ── Public API ────────────────────────────────────────────────────────────────
def _apply_scope(f, strict=False):
    """Resolve user scope + company-warehouse list and stash it onto f.

    Mutates `f` in place by adding `_allowed_warehouses` (list[str] or None).
    Behaviour:
      * System Manager / unrestricted user with company filter → all warehouses
        of that company (so the dashboard shows the full company, not just one
        store).
      * Scoped user → intersection of company-warehouses and CH User Scope
        allowed_warehouses.
      * Explicit store/zone/city filter takes precedence (handled in _build_where).
    """
    try:
        from ch_erp15.ch_erp15.scope import intersect_filters
        eff = intersect_filters(
            company=f.get("company"),
            city=f.get("city"),
            zone=f.get("zone"),
            store=f.get("store"),
        )
    except Exception:
        if strict:
            frappe.throw(_("IMEI store scope cannot be verified."), frappe.PermissionError)
        f["_allowed_warehouses"] = None
        return

    if f.get("store") or f.get("zone") or f.get("city"):
        # Explicit filter wins; _build_where already handles it
        f["_allowed_warehouses"] = None
        return

    if eff.get("bypass"):
        # Unrestricted user: still scope by company (if provided) so the dashboard
        # shows all warehouses of THAT company, not the entire system.
        if f.get("company"):
            company_wh = frappe.get_all(
                "Warehouse",
                filters={"company": f["company"], "disabled": 0},
                pluck="name",
            )
            f["_allowed_warehouses"] = company_wh or ["__none__"]
        else:
            f["_allowed_warehouses"] = None  # truly unrestricted
        return

    # Scoped user: use intersected allowed_warehouses
    aw = eff.get("allowed_warehouses")
    if aw is None:
        # Scoped but no warehouse constraint resolved: fall back to company
        if f.get("company"):
            aw = frappe.get_all(
                "Warehouse",
                filters={"company": f["company"], "disabled": 0},
                pluck="name",
            )
    f["_allowed_warehouses"] = aw or []


@frappe.whitelist()
def get_imei_tracker_data(filters=None):
    """Return KPIs + table rows for the IMEI Tracker hub."""
    f = _norm_filters(filters)
    _apply_scope(f)
    where, params = _build_where(f)

    # ── KPIs (counts per bucket) ──
    kpi_sql = f"""
        SELECT lc.lifecycle_status, COUNT(*) AS cnt
        {_join_clause()}
        WHERE {where}
        GROUP BY lc.lifecycle_status
    """
    rows = frappe.db.sql(kpi_sql, params, as_dict=True)
    by_status = {r.lifecycle_status: cint(r.cnt) for r in rows}

    bucket_counts = {b: 0 for b in STATUS_BUCKETS}
    for status, cnt in by_status.items():
        for bucket, statuses in STATUS_BUCKETS.items():
            if status in statuses:
                bucket_counts[bucket] += cnt

    total = sum(by_status.values())

    # MTD sold
    mtd_params = dict(params)
    mtd_params["mtd_start"] = today()[:8] + "01"
    mtd_sold_sql = f"""
        SELECT COUNT(*) AS cnt
        {_join_clause()}
        WHERE {where}
          AND lc.lifecycle_status IN ('Sold','Delivered')
          AND lc.modified >= %(mtd_start)s
    """
    mtd_sold = cint(frappe.db.sql(mtd_sold_sql, mtd_params, as_dict=True)[0].cnt)

    # Serial-kind split (tri-state, without the kind filter applied)
    f_no_kind = dict(f)
    f_no_kind.pop("kind", None)
    f_no_kind.pop("imei_only", None)
    f_no_kind.pop("non_imei_only", None)
    where_no_kind, params_no_kind = _build_where(f_no_kind)
    kind_split = frappe.db.sql(
        f"SELECT {SERIAL_KIND_EXPR} AS serial_kind, COUNT(*) AS cnt "
        f"{_join_clause()} WHERE {where_no_kind} GROUP BY {SERIAL_KIND_EXPR}",
        params_no_kind, as_dict=True,
    )
    kind_counts = {"IMEI": 0, "Barcode": 0, "UOM": 0}
    for r in kind_split:
        k = (r.serial_kind or "").strip()
        if k in kind_counts:
            kind_counts[k] += cint(r.cnt)
        else:
            # Defensive: any unexpected legacy value rolls into Barcode bucket
            kind_counts["Barcode"] += cint(r.cnt)
    real_imei_count = kind_counts["IMEI"]
    non_imei_count = kind_counts["Barcode"] + kind_counts["UOM"]

    kpis = [
        {"key": "total",        "label": "Total Serials",   "value": total,                             "color": "#6366f1"},
        {"key": "real_imei",    "label": "IMEI",            "value": kind_counts["IMEI"],               "color": "#0891b2"},
        {"key": "barcode",      "label": "Barcode",         "value": kind_counts["Barcode"],            "color": "#64748b"},
        {"key": "uom",          "label": "UOM",             "value": kind_counts["UOM"],                "color": "#a16207"},
        {"key": "non_imei",     "label": "Non-IMEI",        "value": non_imei_count,                    "color": "#94a3b8"},
        {"key": "in_stock",     "label": "In Stock",        "value": bucket_counts["in_stock"],         "color": "#10b981"},
        {"key": "sold_mtd",     "label": "Sold MTD",        "value": mtd_sold,                          "color": "#3b82f6"},
        {"key": "in_service",   "label": "In Service",      "value": bucket_counts["in_service"],       "color": "#f59e0b"},
        {"key": "returned",     "label": "Returned",        "value": bucket_counts["returned"],         "color": "#ef4444"},
        {"key": "bought_back",  "label": "Bought Back",     "value": bucket_counts["bought_back"],      "color": "#8b5cf6"},
        {"key": "out_of_pool",  "label": "Out of Pool",     "value": bucket_counts["out_of_pool"],      "color": "#64748b"},
    ]

    # ── Buckets with drill-through metadata ──
    buckets = [
        {"key": k, "label": BUCKET_LABELS[k], "count": bucket_counts[k]}
        for k in STATUS_BUCKETS
    ]

    # ── Table rows (server-side pagination: LIMIT <offset>, <page_length>) ──
    page_length = f.get("page_length") or 200
    if page_length <= 0:
        page_length = 200
    start = f.get("start") or 0
    list_sql = f"""
        SELECT
            lc.serial_no,
            lc.imei_number,
            lc.imei_number_2,
            IFNULL(sn.ch_is_imei, 0) AS is_imei,
            {SERIAL_KIND_EXPR} AS serial_kind,
            lc.item_code,
            itm.item_name,
            itm.brand,
            lc.lifecycle_status,
            lc.sub_status,
            lc.stock_condition,
            lc.current_company,
            lc.current_warehouse,
            wh.warehouse_name AS warehouse_name,
            wh.ch_city  AS city,
            wh.ch_zone  AS zone,
            lc.current_store,
            lc.purchase_date,
            lc.purchase_rate,
            lc.sale_rate,
            lc.warranty_status,
            lc.last_service_date,
            lc.last_service_type,
            lc.modified AS last_updated,
            DATEDIFF(%(today)s, lc.purchase_date)  AS age_days,
            DATEDIFF(%(today)s, lc.modified)        AS days_since_change
        {_join_clause()}
        WHERE {where}
        ORDER BY lc.modified DESC
        LIMIT {start}, {page_length}
    """
    list_params = dict(params, today=today())
    rows = frappe.db.sql(list_sql, list_params, as_dict=True)

    # Tag aging flags on rows
    unsold_stale_days = get_int_setting("imei_unsold_stale_days", 90)
    service_stale_days = get_int_setting("imei_service_stale_days", 30)
    for r in rows:
        r["aging_flags"] = []
        if (
            r["lifecycle_status"] in ("Received", "In Stock", "Displayed")
            and (r["age_days"] or 0) > unsold_stale_days
        ):
            r["aging_flags"].append("unsold_90")
        if (
            r["lifecycle_status"] in ("In Service", "Repaired")
            and (r["days_since_change"] or 0) > service_stale_days
        ):
            r["aging_flags"].append("in_service_30")

    return {
        "kpis":   kpis,
        "buckets": buckets,
        "rows":   rows,
        "total_rows": total,
        "shown_rows": len(rows),
        "start": start,
        "page_length": page_length,
    }


_IMEI_VIEW_ROLES = (
    "CH Master Manager",
    "CH Warranty Manager",
    "CH Viewer",
    "Stock User",
)
_IMEI_BULK_ROLES = ("Stock Manager", "CH Master Manager")


def _require_imei_view_access():
    if not (
        has_role_setting("app_access_roles", _IMEI_VIEW_ROLES)
        or has_role_setting("bulk_imei_roles", _IMEI_BULK_ROLES)
    ):
        frappe.throw(_("You do not have permission to view IMEI history."), frappe.PermissionError)


def _require_named_read(doctype, name):
    if not frappe.has_permission(
        doctype,
        ptype="read",
        doc=name,
        user=frappe.session.user,
        throw=False,
    ):
        frappe.throw(
            _("You do not have permission to read {0} {1}.").format(doctype, name),
            frappe.PermissionError,
        )


def _has_named_read(doctype, name):
    try:
        return bool(frappe.has_permission(
            doctype,
            ptype="read",
            doc=name,
            user=frappe.session.user,
            throw=False,
        ))
    except Exception:
        return False


def _serial_values(value):
    return {
        part.strip()
        for part in re.split(r"[\n,]+", value or "")
        if part.strip()
    }


def _get_permitted_buyback_rows(doctype, serial_field, serial_no, fields, limit):
    if not frappe.db.exists("DocType", doctype):
        return []
    rows = []
    for name in frappe.get_all(
        doctype,
        filters={serial_field: serial_no},
        pluck="name",
        order_by="creation desc",
        limit_page_length=limit,
    ):
        if not _has_named_read(doctype, name):
            continue
        row = frappe.db.get_value(doctype, name, fields, as_dict=True)
        if row:
            row["doctype"] = doctype
            rows.append(row)
    return rows


@frappe.whitelist()
def get_imei_history(serial_no=None, imei=None):
    """Return a bounded, named-permission and store-scoped serial history."""
    _require_imei_view_access()
    key = str(serial_no or imei or "").strip()
    if not key or len(key) > 140:
        frappe.throw(_("Provide a valid serial_no or imei."), frappe.ValidationError)

    lifecycle_name = None
    for filters in (
        {"serial_no": key},
        {"imei_number": key},
        {"imei_number_2": key},
    ):
        lifecycle_name = frappe.db.get_value("CH Serial Lifecycle", filters, "name")
        if lifecycle_name:
            break

    lifecycle = None
    if lifecycle_name:
        _require_named_read("CH Serial Lifecycle", lifecycle_name)
        lifecycle = frappe.db.get_value(
            "CH Serial Lifecycle",
            lifecycle_name,
            [
                "name", "serial_no", "ch_serial_kind", "imei_number", "imei_number_2",
                "item_code", "item_name", "lifecycle_status", "sub_status",
                "stock_condition", "current_company", "current_warehouse", "current_store",
                "warranty_status", "purchase_date", "sale_date", "customer",
            ],
            as_dict=True,
        )

    sn_name = (
        lifecycle.get("serial_no") if lifecycle else None
    ) or (key if frappe.db.exists("Serial No", key) else None)
    if not sn_name:
        return {"error": _("No record found for {0}").format(key), "key": key}

    _require_named_read("Serial No", sn_name)
    serial = frappe.db.get_value(
        "Serial No",
        sn_name,
        [
            "name", "item_code", "item_name", "warehouse", "status", "brand",
            "item_group", "ch_serial_kind", "ch_is_imei", "ch_buyback_status",
            "ch_buyback_order", "ch_buyback_date", "ch_buyback_price",
            "ch_buyback_grade", "ch_buyback_count", "ch_buyback_customer",
        ],
        as_dict=True,
    )
    if not serial:
        frappe.throw(_("Serial No {0} no longer exists.").format(sn_name), frappe.DoesNotExistError)

    warehouse = lifecycle.get("current_warehouse") if lifecycle else None
    lifecycle_company = lifecycle.get("current_company") if lifecycle else None
    if warehouse and serial.warehouse and warehouse != serial.warehouse:
        frappe.throw(_("Serial warehouse references are inconsistent."), frappe.ValidationError)
    warehouse = warehouse or serial.warehouse
    company = None
    if warehouse:
        _require_named_read("Warehouse", warehouse)
        warehouse_data = frappe.db.get_value(
            "Warehouse", warehouse, ["company", "disabled"], as_dict=True
        )
        if not warehouse_data or warehouse_data.disabled:
            frappe.throw(_("The serial warehouse is not active."), frappe.PermissionError)
        company = warehouse_data.company
    if lifecycle_company and company and lifecycle_company != company:
        frappe.throw(_("Serial company references are inconsistent."), frappe.ValidationError)
    company = lifecycle_company or company
    if company:
        _require_named_read("Company", company)

    if not is_privileged_user() and (not company or not warehouse):
        frappe.throw(
            _("The serial's exact company and warehouse cannot be verified."),
            frappe.PermissionError,
        )
    scope_filters = {"company": company}
    _apply_scope(scope_filters, strict=True)
    allowed_warehouses = scope_filters.get("_allowed_warehouses")
    if allowed_warehouses is not None and warehouse not in set(allowed_warehouses):
        frappe.throw(_("You are not permitted to view this serial's location."), frappe.PermissionError)

    history_limit = min(get_int_setting("bulk_imei_limit", 200, minimum=1), 200)
    query_limit = history_limit
    serial_pattern = (
        rf"(^|[\r\n,])[[:space:]]*{re.escape(sn_name)}[[:space:]]*($|[\r\n,])"
    )
    stock_candidates = frappe.db.sql(
        f"""
        SELECT name, serial_no, posting_date, posting_time, voucher_type, voucher_no,
               warehouse, actual_qty, qty_after_transaction, valuation_rate
        FROM `tabStock Ledger Entry`
        WHERE serial_no REGEXP %(serial_pattern)s
        ORDER BY posting_date DESC, posting_time DESC, creation DESC
        LIMIT {query_limit}
        """,
        {"serial_pattern": serial_pattern},
        as_dict=True,
    )
    stock_movements = []
    for row in stock_candidates:
        if sn_name not in _serial_values(row.serial_no) or not _has_named_read(
            "Stock Ledger Entry", row.name
        ):
            continue
        row.pop("serial_no", None)
        stock_movements.append(row)
        if len(stock_movements) >= history_limit:
            break

    invoice_limit = min(history_limit, 100)
    sales_candidates = frappe.db.sql(
        f"""
        SELECT si.name, si.posting_date, si.customer, si.customer_name,
               si.grand_total, sii.item_code, sii.qty, sii.rate, sii.serial_no
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE sii.serial_no REGEXP %(serial_pattern)s AND si.docstatus = 1
        ORDER BY si.posting_date DESC, si.creation DESC LIMIT {query_limit}
        """,
        {"serial_pattern": serial_pattern},
        as_dict=True,
    )
    sales = []
    sales_permissions = {}
    for row in sales_candidates:
        if row.name not in sales_permissions:
            sales_permissions[row.name] = _has_named_read("Sales Invoice", row.name)
        if sn_name not in _serial_values(row.serial_no) or not sales_permissions[row.name]:
            continue
        row.pop("serial_no", None)
        sales.append(row)
        if len(sales) >= invoice_limit:
            break

    pos_candidates = frappe.db.sql(
        f"""
        SELECT pi.name, pi.posting_date, pi.customer, pi.customer_name,
               pi.grand_total, pii.item_code, pii.qty, pii.rate, pii.serial_no
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        WHERE pii.serial_no REGEXP %(serial_pattern)s AND pi.docstatus = 1
        ORDER BY pi.posting_date DESC, pi.creation DESC LIMIT {query_limit}
        """,
        {"serial_pattern": serial_pattern},
        as_dict=True,
    )
    pos_sales = []
    pos_permissions = {}
    for row in pos_candidates:
        if row.name not in pos_permissions:
            pos_permissions[row.name] = _has_named_read("POS Invoice", row.name)
        if sn_name not in _serial_values(row.serial_no) or not pos_permissions[row.name]:
            continue
        row.pop("serial_no", None)
        pos_sales.append(row)
        if len(pos_sales) >= invoice_limit:
            break

    audit = []
    if lifecycle_name:
        audit = frappe.db.sql(
            f"""
            SELECT log_timestamp, from_status, to_status, changed_by,
                   company, warehouse, remarks
            FROM `tabCH Serial Lifecycle Log`
            WHERE parent = %(parent)s
            ORDER BY log_timestamp DESC
            LIMIT {history_limit}
            """,
            {"parent": lifecycle_name},
            as_dict=True,
        )

    buyback_limit = min(history_limit, 50)
    aggregated = {
        "assessments": _get_permitted_buyback_rows(
            "Buyback Assessment", "imei_serial", sn_name,
            [
                "name", "assessment_id", "customer", "customer_name", "store",
                "item", "item_name", "quoted_price", "estimated_price", "status", "creation",
            ],
            buyback_limit,
        ),
        "inspections": _get_permitted_buyback_rows(
            "Buyback Inspection", "imei_serial", sn_name,
            [
                "name", "inspection_id", "customer", "customer_name", "item",
                "item_name", "status", "condition_grade", "revised_price",
                "diagnostic_source", "creation",
            ],
            buyback_limit,
        ),
        "orders": _get_permitted_buyback_rows(
            "Buyback Order", "imei_serial", sn_name,
            [
                "name", "order_id", "customer", "customer_name", "store", "item",
                "item_name", "final_price", "condition_grade", "status",
                "payment_status", "creation",
            ],
            buyback_limit,
        ),
        "exchanges": _get_permitted_buyback_rows(
            "Buyback Exchange Order", "old_imei_serial", sn_name,
            [
                "name", "exchange_id", "customer", "old_item", "new_item",
                "buyback_amount", "amount_to_pay", "status", "creation",
            ],
            buyback_limit,
        ),
        "timeline": [],
        "audit_log": [],
    }
    if _has_named_read("Serial No", sn_name):
        comment_rows = frappe.get_all(
            "Comment",
            filters={
                "reference_doctype": "Serial No",
                "reference_name": sn_name,
                "comment_type": "Info",
            },
            fields=["name", "content", "comment_by", "creation"],
            order_by="creation desc",
            limit_page_length=min(history_limit, 50),
        )
        aggregated["timeline"] = [
            row for row in comment_rows if _has_named_read("Comment", row.name)
        ]
    order_names = [row.name for row in aggregated["orders"]]
    if order_names and frappe.db.exists("DocType", "Buyback Audit Log"):
        for name in frappe.get_all(
            "Buyback Audit Log",
            filters={
                "reference_doctype": "Buyback Order",
                "reference_name": ["in", order_names],
            },
            pluck="name",
            order_by="timestamp desc",
            limit_page_length=min(history_limit, 100),
        ):
            if not _has_named_read("Buyback Audit Log", name):
                continue
            row = frappe.db.get_value(
                "Buyback Audit Log",
                name,
                ["name", "action", "reference_name", "user", "timestamp", "old_value", "new_value"],
                as_dict=True,
            )
            if row:
                aggregated["audit_log"].append(row)

    timeline = []
    for event in stock_movements:
        timeline.append({
            "kind": "stock",
            "ts": f"{event.posting_date} {event.posting_time}",
            "summary": f"{event.voucher_type} {event.voucher_no} — qty {event.actual_qty} @ {event.warehouse}",
            "doc_type": event.voucher_type,
            "doc_name": event.voucher_no,
        })
    for event in sales:
        timeline.append({
            "kind": "sale",
            "ts": str(event.posting_date),
            "summary": f"Sales Invoice {event.name} — {event.customer_name or event.customer} ₹{event.grand_total}",
            "doc_type": "Sales Invoice",
            "doc_name": event.name,
        })
    for event in pos_sales:
        timeline.append({
            "kind": "pos",
            "ts": str(event.posting_date),
            "summary": f"POS Invoice {event.name} — {event.customer_name or event.customer} ₹{event.grand_total}",
            "doc_type": "POS Invoice",
            "doc_name": event.name,
        })
    for event in audit:
        timeline.append({
            "kind": "lifecycle",
            "ts": str(event.log_timestamp),
            "summary": f"{event.from_status or '∅'} → {event.to_status} by {event.changed_by} — {event.remarks or ''}",
        })
    for key_name in ("assessments", "inspections", "orders", "exchanges"):
        for event in aggregated[key_name]:
            timeline.append({
                "kind": key_name,
                "ts": str(event.get("creation") or ""),
                "summary": f"{key_name.title()}: {event.get('name')} — {event.get('status') or ''}",
                "doc_type": event.get("doctype"),
                "doc_name": event.get("name"),
            })
    timeline.sort(key=lambda event: event["ts"], reverse=True)
    timeline = timeline[: history_limit * 4]

    return {
        "key": sn_name,
        "serial_no": serial,
        "lifecycle": lifecycle,
        "stock_movements": stock_movements,
        "sales": sales,
        "pos_sales": pos_sales,
        "buyback": aggregated,
        "audit": audit,
        "timeline": timeline,
    }


@frappe.whitelist()
def export_imei_data(filters=None, file_format="csv"):
    """Export filtered IMEI tracker data as CSV/XLSX (returned as data URL).

    Returns: { filename, content_b64, mime }
    """
    import base64

    f = _norm_filters(filters)
    _apply_scope(f)
    where, params = _build_where(f)

    sql = f"""
        SELECT
            lc.serial_no                       AS Serial,
            {SERIAL_KIND_EXPR}                 AS Type,
            lc.imei_number                     AS IMEI_1,
            lc.imei_number_2                   AS IMEI_2,
            lc.item_code                       AS Item_Code,
            itm.item_name                      AS Item_Name,
            itm.brand                          AS Brand,
            lc.lifecycle_status                AS Status,
            lc.sub_status                      AS Sub_Status,
            lc.stock_condition                 AS Condition,
            lc.current_company                 AS Company,
            wh.ch_city                         AS City,
            wh.ch_zone                         AS Zone,
            lc.current_warehouse               AS Warehouse,
            wh.warehouse_name                  AS Warehouse_Name,
            lc.current_store                   AS Store,
            lc.purchase_date                   AS Purchase_Date,
            lc.purchase_rate                   AS Purchase_Rate,
            lc.sale_rate                       AS Sale_Rate,
            lc.warranty_status                 AS Warranty_Status,
            lc.last_service_date               AS Last_Service,
            lc.last_service_type               AS Last_Service_Type,
            DATEDIFF(%(today)s, lc.purchase_date)  AS Age_Days,
            DATEDIFF(%(today)s, lc.modified)        AS Days_Since_Change,
            lc.modified                        AS Last_Updated
        {_join_clause()}
        WHERE {where}
        ORDER BY lc.modified DESC
        LIMIT 50000
    """
    params["today"] = today()
    rows = frappe.db.sql(sql, params, as_dict=True)

    if not rows:
        rows = [{"Serial": "", "Type": "", "IMEI_1": "", "IMEI_2": "",
                 "Item_Code": "", "Item_Name": "", "Brand": "", "Status": "",
                 "Sub_Status": "", "Condition": "", "Company": "",
                 "City": "", "Zone": "", "Warehouse": "", "Warehouse_Name": "",
                 "Store": "", "Purchase_Date": "", "Purchase_Rate": "",
                 "Sale_Rate": "", "Warranty_Status": "", "Last_Service": "",
                 "Last_Service_Type": "", "Age_Days": "", "Days_Since_Change": "",
                 "Last_Updated": ""}]

    headers = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for r in rows:
        # Stringify dates / Decimals for CSV friendliness
        writer.writerow({k: ("" if v is None else cstr(v)) for k, v in r.items()})

    content = buf.getvalue()
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return {
        "filename": f"imei_tracker_{nowdate()}.csv",
        "content_b64": encoded,
        "mime": "text/csv",
        "row_count": len(rows),
    }


@frappe.whitelist(methods=["POST"])
def bulk_update_status(serial_nos, new_status, remarks=""):
    """Bulk-update the lifecycle status of selected serial / IMEI records.

    Allowed admin transitions only — Scrapped / Lost / In Stock (recovery).
    Logs each transition to CH Serial Lifecycle Log for audit.
    """
    allowed_statuses = get_list_setting(
        "bulk_imei_target_statuses",
        ("Scrapped", "Lost", "In Stock"),
    )
    if new_status not in allowed_statuses:
        frappe.throw(_("Bulk transition not allowed for status: {0}").format(new_status))

    if isinstance(serial_nos, str):
        try:
            serial_nos = json.loads(serial_nos)
        except Exception:
            serial_nos = [s.strip() for s in serial_nos.split(",") if s.strip()]

    if not serial_nos:
        frappe.throw(_("No serial numbers provided."))

    serial_nos = list(dict.fromkeys(str(value).strip() for value in serial_nos if str(value).strip()))
    limit = get_int_setting("bulk_imei_limit", 200, minimum=1)
    if len(serial_nos) > limit:
        frappe.throw(
            _("A maximum of {0} serial numbers can be updated at once.").format(limit),
            frappe.ValidationError,
        )

    require_role_setting(
        "bulk_imei_roles",
        defaults=("System Manager", "Stock Manager", "CH Master Manager"),
        action=_("perform bulk IMEI status updates"),
    )

    lifecycle_docs = {}
    for sn in serial_nos:
        if not frappe.db.exists("CH Serial Lifecycle", sn):
            continue
        lc = frappe.get_doc("CH Serial Lifecycle", sn)
        if not is_privileged_user():
            try:
                from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
            except (ImportError, ModuleNotFoundError):
                frappe.throw(_("Store scope cannot be verified."), frappe.PermissionError)
            assert_user_has_store_scope(
                company=lc.current_company,
                warehouse=lc.current_warehouse,
            )
        lifecycle_docs[sn] = lc

    updated = 0
    skipped = []
    from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
        update_lifecycle_status,
    )

    for sn in serial_nos:
        if sn not in lifecycle_docs:
            skipped.append({"serial_no": sn, "reason": "lifecycle row missing"})
            continue
        if lifecycle_docs[sn].lifecycle_status == new_status:
            continue
        result = update_lifecycle_status(
            sn,
            new_status,
            remarks=remarks or _("Bulk update via IMEI Tracker"),
        )
        if result.get("status") == "ok":
            updated += 1
        else:
            skipped.append({"serial_no": sn, "reason": result.get("reason") or "not updated"})

    return {"updated": updated, "skipped": skipped}


def backfill_is_imei_flag():
    """Recompute Serial No.ch_serial_kind, Serial No.ch_is_imei, and
    CH Serial Lifecycle.ch_serial_kind from the authoritative
    Item.ch_serial_kind classification.

    Order of operations (idempotent, safe to re-run):
      1. Promote Item.ch_serial_kind → Serial No.ch_serial_kind when blank.
      2. Heuristic backfill for Serial No rows whose item kind is also blank:
         15-digit-numeric serial OR lifecycle has IMEI numbers OR appears in
         Buyback Order → IMEI; otherwise leave as is (admins must classify).
      3. Re-derive Serial No.ch_is_imei as a boolean compatibility flag
         (= 1 only when ch_serial_kind = 'IMEI').
      4. Mirror Serial No.ch_serial_kind onto CH Serial Lifecycle.ch_serial_kind.

    Returns kind + boolean counts so the operator can sanity-check.
    """
    # 1) Promote Item kind onto Serial No where missing
    frappe.db.sql(
        """
        UPDATE `tabSerial No` sn
          JOIN `tabItem` i ON i.name = sn.item_code
           SET sn.ch_serial_kind = i.ch_serial_kind
         WHERE (sn.ch_serial_kind IS NULL OR sn.ch_serial_kind = '')
           AND i.ch_serial_kind IN ('IMEI', 'Barcode', 'UOM')
        """
    )

    # 2) Heuristic IMEI detection for still-blank rows
    frappe.db.sql(
        """
        UPDATE `tabSerial No` sn
          LEFT JOIN `tabCH Serial Lifecycle` lc ON lc.serial_no = sn.name
           SET sn.ch_serial_kind = 'IMEI'
         WHERE (sn.ch_serial_kind IS NULL OR sn.ch_serial_kind = '')
           AND (
                (LENGTH(sn.name) = 15 AND sn.name REGEXP '^[0-9]{15}$')
             OR (lc.imei_number   IS NOT NULL AND lc.imei_number   <> '')
             OR (lc.imei_number_2 IS NOT NULL AND lc.imei_number_2 <> '')
           )
        """
    )

    if frappe.db.table_exists("Buyback Order"):
        frappe.db.sql(
            """
            UPDATE `tabSerial No` sn
              JOIN `tabBuyback Order` bo ON bo.imei_serial = sn.name
               SET sn.ch_serial_kind = 'IMEI'
             WHERE (sn.ch_serial_kind IS NULL OR sn.ch_serial_kind = '')
            """
        )

    # 3) Re-derive boolean compatibility flag
    frappe.db.sql(
        """
        UPDATE `tabSerial No`
           SET ch_is_imei = CASE WHEN ch_serial_kind = 'IMEI' THEN 1 ELSE 0 END
         WHERE IFNULL(ch_is_imei, -1) <>
               CASE WHEN ch_serial_kind = 'IMEI' THEN 1 ELSE 0 END
        """
    )

    # 4) Mirror onto CH Serial Lifecycle
    if frappe.db.has_column("CH Serial Lifecycle", "ch_serial_kind"):
        frappe.db.sql(
            """
            UPDATE `tabCH Serial Lifecycle` lc
              JOIN `tabSerial No` sn ON sn.name = lc.serial_no
               SET lc.ch_serial_kind = sn.ch_serial_kind
             WHERE sn.ch_serial_kind IN ('IMEI', 'Barcode', 'UOM')
               AND (lc.ch_serial_kind IS NULL
                    OR lc.ch_serial_kind = ''
                    OR lc.ch_serial_kind <> sn.ch_serial_kind)
            """
        )

    frappe.db.commit()  # noqa: SQL003 — maintenance utility

    kind_counts = frappe.db.sql(
        "SELECT IFNULL(ch_serial_kind,'') AS kind, COUNT(*) AS cnt "
        "FROM `tabSerial No` GROUP BY IFNULL(ch_serial_kind,'')",
        as_dict=True,
    )
    bool_counts = frappe.db.sql(
        "SELECT IFNULL(ch_is_imei,0) AS is_imei, COUNT(*) AS cnt "
        "FROM `tabSerial No` GROUP BY IFNULL(ch_is_imei,0)",
        as_dict=True,
    )
    return {"kind_counts": kind_counts, "bool_counts": bool_counts}
