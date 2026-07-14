"""Controlled opening-stock import via Stock Reconciliation.

This module is intentionally bench-executable and conservative. It avoids
enabling generic Data Import for Stock Reconciliation, but still posts through
ERPNext's standard Stock Reconciliation document so Stock Ledger, GL, Serial No,
and CH lifecycle hooks stay aligned.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

import erpnext
import frappe
from frappe.utils import cint, flt, now_datetime
from erpnext.stock.utils import get_stock_balance

from ch_item_master.ch_core.bin_transfer import BIN_TYPES, get_store_bin


REQUIRED_INPUT_HINT = (
	"Required columns: item_code, qty, valuation_rate, and either warehouse "
	"or store + bin_type. Optional: company, serial_no, batch_no, "
	"allow_zero_valuation_rate."
)

TEMPLATE_COLUMNS = [
	"company",
	"store",
	"bin_type",
	"warehouse",
	"item_code",
	"qty",
	"valuation_rate",
	"serial_no",
	"batch_no",
	"allow_zero_valuation_rate",
]

SERIAL_SPLIT_RE = re.compile(r"[\n\r,;|]+")


@dataclass
class ImportLine:
	row_no: int
	company: str
	warehouse: str
	item_code: str
	qty: float
	valuation_rate: float
	serials: tuple[str, ...] = ()
	batch_no: str = ""
	allow_zero_valuation_rate: bool = False
	store: str = ""
	bin_type: str = ""
	has_serial_no: bool = False
	has_batch_no: bool = False
	stock_uom: str = ""


@dataclass
class GroupedLine:
	company: str
	warehouse: str
	item_code: str
	qty: float
	valuation_rate: float
	serials: list[str] = field(default_factory=list)
	batch_no: str = ""
	allow_zero_valuation_rate: bool = False
	source_rows: list[int] = field(default_factory=list)
	has_serial_no: bool = False
	has_batch_no: bool = False
	stock_uom: str = ""


def get_template() -> dict:
	"""Return the CSV template definition for Desk/bench callers."""
	return {
		"columns": TEMPLATE_COLUMNS,
		"notes": [
			REQUIRED_INPUT_HINT,
			"Use store + bin_type for CH POS stock. Valid bin_type values: Sellable, Damaged, Demo, Buyback.",
			"Do not load In-Transit as opening bin stock; create transfer manifests if cutover transit is required.",
			"For serialized items, serial_no can be one IMEI or multiple IMEIs separated by newline/comma/semicolon/pipe.",
		],
		"example_rows": [
			{
				"company": "BestBuy Mobiles Pvt Ltd",
				"store": "STO-BMPL-CHENNA-0004",
				"bin_type": "Sellable",
				"warehouse": "",
				"item_code": "IP000001-BLA-256GB",
				"qty": "1",
				"valuation_rate": "62000",
				"serial_no": "356002001000001",
				"batch_no": "",
				"allow_zero_valuation_rate": "0",
			}
		],
	}


def import_opening_stock(
	csv_path: str,
	company: str | None = None,
	posting_date: str | None = None,
	posting_time: str = "00:00:00",
	expense_account: str | None = None,
	cost_center: str | None = None,
	dry_run: int = 1,
	submit: int = 0,
	max_rows_per_doc: int = 100,
	group_by: str = "warehouse",
	allow_existing_stock: int = 0,
	allow_existing_batch: int = 0,
) -> dict:
	"""Import opening stock rows from CSV into Stock Reconciliation documents.

	Args:
		csv_path: Absolute path, bench-relative path, or site files path.
		company: Optional company override. If set, all CSV rows must match it.
		posting_date/posting_time: Cutover timestamp for the opening entry.
		expense_account: Opening/difference account. For perpetual inventory this
			must be a Balance Sheet account.
		cost_center: Cost center used for GL entries when required.
		dry_run: 1 validates and plans only. 0 writes documents.
		submit: When writing, submit the created Stock Reconciliation documents.
		max_rows_per_doc: Child rows per document. Capped at 100 to avoid queued submit.
		group_by: "warehouse" (default) or "company".
		allow_existing_stock: 0 blocks rows where stock already exists at cutover.
		allow_existing_batch: 0 blocks rerun if this batch id already exists.
	"""
	dry_run = cint(dry_run)
	submit = cint(submit)
	allow_existing_stock = cint(allow_existing_stock)
	allow_existing_batch = cint(allow_existing_batch)
	max_rows_per_doc = max(1, min(cint(max_rows_per_doc or 100), 100))
	group_by = (group_by or "warehouse").strip().lower()
	if group_by not in {"warehouse", "company"}:
		group_by = "warehouse"

	if not posting_date:
		return _error_result("posting_date is required for go-live stock import.")

	path = _resolve_path(csv_path)
	if not path or not os.path.exists(path):
		return _error_result(f"CSV file not found: {csv_path}")

	raw_rows, file_hash = _read_csv(path)
	batch_id = _batch_id(file_hash, company, posting_date, posting_time)
	existing_docs = _existing_docs_for_batch(batch_id)

	result = {
		"ok": False,
		"dry_run": bool(dry_run),
		"submitted": bool(submit),
		"batch_id": batch_id,
		"source_path": path,
		"csv_rows": len(raw_rows),
		"grouped_rows": 0,
		"document_count": 0,
		"created": [],
		"existing_docs": existing_docs,
		"errors": [],
		"warnings": [],
	}

	if existing_docs and not allow_existing_batch:
		result["errors"].append(
			"Existing Stock Reconciliation documents were found for this batch_id. "
			"Review/cancel them before rerun, or pass allow_existing_batch=1 intentionally."
		)
		return result

	lines, errors, warnings = _normalise_rows(raw_rows, company)
	result["errors"].extend(errors)
	result["warnings"].extend(warnings)
	if result["errors"]:
		return result

	grouped, group_errors = _group_lines(lines)
	result["errors"].extend(group_errors)
	if result["errors"]:
		return result

	if not allow_existing_stock:
		result["errors"].extend(_validate_no_existing_stock(grouped, posting_date, posting_time))
		if result["errors"]:
			return result

	docs = _make_doc_plans(
		grouped,
		posting_date=posting_date,
		posting_time=posting_time,
		expense_account=expense_account,
		cost_center=cost_center,
		max_rows_per_doc=max_rows_per_doc,
		group_by=group_by,
	)
	result["grouped_rows"] = len(grouped)
	result["document_count"] = len(docs)

	validation_errors = _validate_doc_plans(docs, submit=submit)
	result["errors"].extend(validation_errors)
	if result["errors"]:
		frappe.db.rollback()
		return result

	result["plan"] = [
		{
			"company": doc.company,
			"warehouse": _single_warehouse(doc),
			"rows": len(doc.items),
			"qty": sum(flt(row.qty) for row in doc.items),
			"serial_count": sum(len(_split_serials(row.get("serial_no"))) for row in doc.items),
		}
		for doc in docs
	]

	if dry_run:
		result["ok"] = True
		result["message"] = "Dry run passed. No Stock Reconciliation documents were created."
		frappe.db.rollback()
		return result

	for doc in docs:
		try:
			doc.flags.ignore_permissions = True
			doc.insert()
			if submit:
				doc.submit()
			doc.add_comment(
				"Info",
				(
					f"CH Opening Stock Import Batch: {batch_id}\n"
					f"Source: {os.path.basename(path)}\n"
					f"Imported at: {now_datetime()}"
				),
			)
			frappe.db.commit()
			result["created"].append(
				{
					"name": doc.name,
					"docstatus": doc.docstatus,
					"company": doc.company,
					"warehouse": _single_warehouse(doc),
					"rows": len(doc.items),
					"qty": sum(flt(row.qty) for row in doc.items),
				}
			)
		except Exception as exc:
			frappe.db.rollback()
			result["errors"].append(f"{getattr(doc, 'name', 'new Stock Reconciliation')}: {exc}")
			result["ok"] = False
			return result

	result["ok"] = True
	result["message"] = (
		"Opening stock documents submitted."
		if submit
		else "Opening stock documents created as Draft. Submit them after review."
	)
	return result


def _resolve_path(csv_path: str) -> str | None:
	if not csv_path:
		return None
	csv_path = os.path.expanduser(str(csv_path).strip())
	candidates = []
	if os.path.isabs(csv_path):
		candidates.append(csv_path)
	else:
		candidates.extend(
			[
				os.path.abspath(csv_path),
				frappe.get_site_path("private", "files", csv_path),
				frappe.get_site_path("public", "files", csv_path),
			]
		)
	for candidate in candidates:
		if os.path.exists(candidate):
			return candidate
	return candidates[0] if candidates else None


def _read_csv(path: str) -> tuple[list[dict], str]:
	with open(path, "rb") as handle:
		content = handle.read()
	file_hash = hashlib.sha256(content).hexdigest()
	text = content.decode("utf-8-sig")
	reader = csv.DictReader(io.StringIO(text))
	rows = []
	for row in reader:
		rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
	return rows, file_hash


def _batch_id(file_hash: str, company: str | None, posting_date: str, posting_time: str) -> str:
	payload = {
		"file_hash": file_hash,
		"company": company or "",
		"posting_date": posting_date,
		"posting_time": posting_time,
	}
	return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _existing_docs_for_batch(batch_id: str) -> list[str]:
	rows = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Stock Reconciliation",
			"content": ["like", f"%CH Opening Stock Import Batch: {batch_id}%"],
		},
		pluck="reference_name",
	)
	return sorted(set(rows))


def _normalise_rows(raw_rows: list[dict], company_override: str | None) -> tuple[list[ImportLine], list[str], list[str]]:
	errors: list[str] = []
	warnings: list[str] = []
	lines: list[ImportLine] = []
	seen_serials: dict[str, int] = {}

	if not raw_rows:
		errors.append("CSV has no data rows.")
		return lines, errors, warnings

	headers = set(raw_rows[0].keys())
	if "item_code" not in headers:
		errors.append(f"Missing item_code column. {REQUIRED_INPUT_HINT}")

	for idx, raw in enumerate(raw_rows, start=2):
		row_errors: list[str] = []
		row_no = idx
		item_code = _cell(raw, "item_code", "item")
		if not item_code:
			row_errors.append("item_code is required")

		row_company = company_override or _cell(raw, "company")
		if company_override and _cell(raw, "company") and _cell(raw, "company") != company_override:
			row_errors.append(f"company must be {company_override}")
		if not row_company:
			row_errors.append("company is required in argument or CSV")
		elif not frappe.db.exists("Company", row_company):
			row_errors.append(f"company does not exist: {row_company}")

		store = _cell(raw, "store", "ch_store", "store_code")
		bin_type = _canonical_bin_type(_cell(raw, "bin_type", "bin")) or ("Sellable" if store else "")
		warehouse = _cell(raw, "warehouse")
		if store:
			store = _resolve_store(store)
			if not store:
				row_errors.append(f"store does not exist: {_cell(raw, 'store', 'ch_store', 'store_code')}")
			elif row_company:
				store_company = frappe.db.get_value("CH Store", store, "company")
				if store_company and store_company != row_company:
					row_errors.append(f"store {store} belongs to {store_company}, not {row_company}")
			if bin_type not in BIN_TYPES:
				row_errors.append(f"invalid bin_type {bin_type}; allowed: {', '.join(BIN_TYPES)}")
			elif store:
				try:
					warehouse = get_store_bin(store, bin_type)
				except Exception as exc:
					row_errors.append(str(exc))
		elif bin_type and bin_type not in BIN_TYPES:
			row_errors.append(f"invalid bin_type {bin_type}; allowed: {', '.join(BIN_TYPES)}")

		if not warehouse:
			row_errors.append("warehouse is required when store is not supplied")
		elif not frappe.db.exists("Warehouse", warehouse):
			row_errors.append(f"warehouse does not exist: {warehouse}")
		else:
			wh = frappe.db.get_value(
				"Warehouse",
				warehouse,
				["company", "is_group", "disabled"],
				as_dict=True,
			)
			if cint(wh.is_group):
				row_errors.append(f"warehouse is a group warehouse: {warehouse}")
			if cint(wh.disabled):
				row_errors.append(f"warehouse is disabled: {warehouse}")
			if row_company and wh.company and wh.company != row_company:
				row_errors.append(f"warehouse {warehouse} belongs to {wh.company}, not {row_company}")

		if bin_type in {"In-Transit", "Reserved", "Disposed"}:
			row_errors.append(f"{bin_type} is not a valid opening stock bin")

		item = None
		if item_code:
			item = frappe.db.get_value(
				"Item",
				item_code,
				["name", "is_stock_item", "disabled", "has_serial_no", "has_batch_no", "stock_uom"],
				as_dict=True,
			)
			if not item:
				row_errors.append(f"item does not exist: {item_code}")
			else:
				if not cint(item.is_stock_item):
					row_errors.append(f"item is not a stock item: {item_code}")
				if cint(item.disabled):
					row_errors.append(f"item is disabled: {item_code}")

		qty = _parse_float(_cell(raw, "qty", "quantity"), row_no, "qty", row_errors)
		valuation_rate = _parse_float(
			_cell(raw, "valuation_rate", "rate", "opening_rate"),
			row_no,
			"valuation_rate",
			row_errors,
		)
		if qty < 0:
			row_errors.append("qty cannot be negative")
		if valuation_rate < 0:
			row_errors.append("valuation_rate cannot be negative")

		allow_zero = _as_bool(_cell(raw, "allow_zero_valuation_rate", "allow_zero_rate"))
		serials = tuple(_split_serials(_cell(raw, "serial_no", "serial", "imei", "imei_serial")))
		batch_no = _cell(raw, "batch_no", "batch")

		if item:
			has_serial = bool(cint(item.has_serial_no))
			has_batch = bool(cint(item.has_batch_no))
			if serials and not has_serial:
				row_errors.append(f"serial_no supplied but item {item_code} is not serialized")
			if has_serial:
				if not serials and qty:
					row_errors.append(f"serialized item {item_code} requires serial_no/IMEI")
				if serials and qty and flt(qty) != len(serials):
					row_errors.append(
						f"qty {qty:g} does not match serial count {len(serials)} for {item_code}"
					)
				if serials:
					qty = float(len(serials))
			if batch_no and not has_batch:
				row_errors.append(f"batch_no supplied but item {item_code} is not batched")
			if has_batch and not batch_no:
				row_errors.append(f"batched item {item_code} requires batch_no")
			if batch_no and frappe.db.exists("Batch", batch_no):
				batch_item = frappe.db.get_value("Batch", batch_no, "item")
				if batch_item and batch_item != item_code:
					row_errors.append(f"batch {batch_no} belongs to {batch_item}, not {item_code}")

		if qty and valuation_rate == 0 and not allow_zero:
			row_errors.append("valuation_rate is required for positive qty; set allow_zero_valuation_rate=1 if intentional")

		for serial in serials:
			if serial in seen_serials:
				row_errors.append(f"duplicate serial_no {serial} also appears on CSV row {seen_serials[serial]}")
			else:
				seen_serials[serial] = row_no
			if item_code and frappe.db.exists("Serial No", serial):
				sn = frappe.db.get_value(
					"Serial No",
					serial,
					["item_code", "warehouse"],
					as_dict=True,
				)
				if sn.item_code and sn.item_code != item_code:
					row_errors.append(f"serial {serial} already exists for item {sn.item_code}")
				if sn.warehouse:
					row_errors.append(f"serial {serial} already has warehouse {sn.warehouse}")

		if row_errors:
			errors.extend([f"Row {row_no}: {msg}" for msg in row_errors])
			continue

		lines.append(
			ImportLine(
				row_no=row_no,
				company=row_company,
				warehouse=warehouse,
				item_code=item_code,
				qty=float(qty),
				valuation_rate=float(valuation_rate),
				serials=serials,
				batch_no=batch_no,
				allow_zero_valuation_rate=allow_zero,
				store=store,
				bin_type=bin_type,
				has_serial_no=bool(cint(item.has_serial_no)) if item else False,
				has_batch_no=bool(cint(item.has_batch_no)) if item else False,
				stock_uom=item.stock_uom if item else "",
			)
		)

	return lines, errors, warnings


def _cell(row: dict, *names: str) -> str:
	for name in names:
		if name in row and row.get(name) is not None:
			return str(row.get(name)).strip()
	return ""


def _resolve_store(value: str) -> str | None:
	if frappe.db.exists("CH Store", value):
		return value
	return frappe.db.get_value("CH Store", {"store_code": value}, "name")


def _canonical_bin_type(value: str) -> str:
	value = (value or "").strip()
	if not value:
		return ""
	lookup = {bin_type.lower(): bin_type for bin_type in BIN_TYPES}
	return lookup.get(value.lower(), value)


def _parse_float(value: str, row_no: int, fieldname: str, errors: list[str]) -> float:
	if value in ("", None):
		return 0.0
	try:
		return float(Decimal(str(value).replace(",", "").strip()))
	except (InvalidOperation, ValueError):
		errors.append(f"{fieldname} must be numeric, got {value!r}")
		return 0.0


def _as_bool(value: str) -> bool:
	return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _split_serials(value: str | None) -> list[str]:
	if not value:
		return []
	return [part.strip() for part in SERIAL_SPLIT_RE.split(str(value)) if part.strip()]


def _group_lines(lines: list[ImportLine]) -> tuple[list[GroupedLine], list[str]]:
	errors: list[str] = []
	groups: dict[tuple, GroupedLine] = {}
	rate_by_key: dict[tuple, float] = {}

	for line in lines:
		if line.has_serial_no:
			key = (
				line.company,
				line.warehouse,
				line.item_code,
				line.batch_no,
				line.valuation_rate,
				"serial",
			)
		elif line.has_batch_no:
			key = (line.company, line.warehouse, line.item_code, line.batch_no, "batch")
		else:
			key = (line.company, line.warehouse, line.item_code, "plain")

		rate_key = key[:-1] if not line.has_serial_no else key[:4] + ("serial",)
		existing_rate = rate_by_key.get(rate_key)
		if existing_rate is not None and flt(existing_rate) != flt(line.valuation_rate):
			errors.append(
				"Rows {0}: conflicting valuation_rate for {1} in {2}. "
				"Stock Reconciliation supports one opening valuation rate per item/warehouse/batch."
				.format(line.row_no, line.item_code, line.warehouse)
			)
			continue
		rate_by_key[rate_key] = line.valuation_rate

		group = groups.get(key)
		if not group:
			group = GroupedLine(
				company=line.company,
				warehouse=line.warehouse,
				item_code=line.item_code,
				qty=0,
				valuation_rate=line.valuation_rate,
				batch_no=line.batch_no,
				allow_zero_valuation_rate=line.allow_zero_valuation_rate,
				has_serial_no=line.has_serial_no,
				has_batch_no=line.has_batch_no,
				stock_uom=line.stock_uom,
			)
			groups[key] = group

		group.source_rows.append(line.row_no)
		group.allow_zero_valuation_rate = group.allow_zero_valuation_rate or line.allow_zero_valuation_rate
		if line.has_serial_no:
			group.serials.extend(line.serials)
			group.qty = float(len(group.serials))
		else:
			group.qty += line.qty

	return list(groups.values()), errors


def _validate_no_existing_stock(
	grouped: Iterable[GroupedLine],
	posting_date: str,
	posting_time: str,
) -> list[str]:
	errors: list[str] = []
	checked: set[tuple[str, str]] = set()
	for row in grouped:
		key = (row.item_code, row.warehouse)
		if key in checked:
			continue
		checked.add(key)
		try:
			balance = get_stock_balance(
				row.item_code,
				row.warehouse,
				posting_date,
				posting_time,
				with_valuation_rate=True,
				with_serial_no=row.has_serial_no,
			)
		except Exception as exc:
			errors.append(f"{row.item_code} / {row.warehouse}: could not read current stock: {exc}")
			continue
		current_qty = flt(balance[0] if isinstance(balance, (tuple, list)) else balance)
		if current_qty:
			errors.append(
				f"{row.item_code} already has qty {current_qty:g} in {row.warehouse} at cutover. "
				"Opening import is blocked unless allow_existing_stock=1."
			)
	return errors


def _make_doc_plans(
	grouped: list[GroupedLine],
	posting_date: str,
	posting_time: str,
	expense_account: str | None,
	cost_center: str | None,
	max_rows_per_doc: int,
	group_by: str,
) -> list:
	buckets: dict[tuple, list[GroupedLine]] = defaultdict(list)
	for row in grouped:
		key = (row.company, row.warehouse) if group_by == "warehouse" else (row.company,)
		buckets[key].append(row)

	docs = []
	for _key, rows in sorted(buckets.items(), key=lambda kv: str(kv[0])):
		rows = sorted(rows, key=lambda r: (r.warehouse, r.item_code, r.batch_no))
		for chunk in _chunks(rows, max_rows_per_doc):
			company = chunk[0].company
			doc = frappe.new_doc("Stock Reconciliation")
			doc.company = company
			doc.purpose = "Opening Stock"
			doc.posting_date = posting_date
			doc.posting_time = posting_time
			doc.set_posting_time = 1
			if expense_account:
				doc.expense_account = expense_account
			if cost_center:
				doc.cost_center = cost_center

			for row in chunk:
				child = doc.append(
					"items",
					{
						"item_code": row.item_code,
						"warehouse": row.warehouse,
						"qty": row.qty,
						"valuation_rate": row.valuation_rate,
						"stock_uom": row.stock_uom,
						"allow_zero_valuation_rate": 1 if row.allow_zero_valuation_rate else 0,
					},
				)
				if row.batch_no:
					child.batch_no = row.batch_no
				if row.has_serial_no or row.has_batch_no:
					child.use_serial_batch_fields = 1
				if row.serials:
					child.serial_no = "\n".join(row.serials)
			docs.append(doc)
	return docs


def _validate_doc_plans(docs: list, submit: int) -> list[str]:
	errors: list[str] = []
	for idx, doc in enumerate(docs, start=1):
		try:
			doc.flags.ignore_permissions = True
			doc._action = "submit" if submit else "save"
			_validate_opening_account(doc)
			doc.validate()
		except Exception as exc:
			errors.append(f"Planned document {idx} ({_single_warehouse(doc)}): {exc}")
	return errors


def _validate_opening_account(doc) -> None:
	if not erpnext.is_perpetual_inventory_enabled(doc.company):
		return
	account = doc.expense_account or frappe.get_cached_value("Company", doc.company, "stock_adjustment_account")
	if not account:
		frappe.throw("expense_account is required for perpetual inventory opening stock.")
	acc = frappe.db.get_value("Account", account, ["company", "is_group", "report_type"], as_dict=True)
	if not acc:
		frappe.throw(f"expense_account does not exist: {account}")
	if acc.company and acc.company != doc.company:
		frappe.throw(f"expense_account {account} belongs to {acc.company}, not {doc.company}")
	if cint(acc.is_group):
		frappe.throw(f"expense_account {account} is a group account")
	if acc.report_type == "Profit and Loss":
		frappe.throw(
			f"expense_account {account} is Profit and Loss. Opening Stock needs a Balance Sheet account."
		)


def _chunks(rows: list[GroupedLine], size: int) -> Iterable[list[GroupedLine]]:
	for idx in range(0, len(rows), size):
		yield rows[idx : idx + size]


def _single_warehouse(doc) -> str:
	warehouses = sorted({row.warehouse for row in doc.items if row.warehouse})
	if not warehouses:
		return ""
	if len(warehouses) == 1:
		return warehouses[0]
	return f"{len(warehouses)} warehouses"


def _error_result(message: str) -> dict:
	return {"ok": False, "errors": [message], "dry_run": True}
