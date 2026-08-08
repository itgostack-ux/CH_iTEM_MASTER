# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Canonical store → Cost Center resolution.

Store-wise P&L is captured with Cost Centers, not with per-store Accounts.
That distinction matters: ``cost_center`` is an extra column on the GL Entry
row, never a split of the account, so consolidated account totals stay complete
no matter how the rows are attributed. Attribution only ever *adds* the ability
to group by store.

The POS path already resolves its Cost Center from ``POS Profile.cost_center``
(see ``ch_pos.api.pos_api``). Every other GL-bearing path — GoFix service
invoices, spare-part Stock Entries, scheme Journal Entries, advance Payment
Entries — used to fall through to ``Company.cost_center`` ("Main - BM"), which
is why 249,549 of 291,987 GL rows carried a single Cost Center.

Resolution order (first hit wins):

1. explicit ``pos_profile`` → its ``cost_center``
2. explicit ``store`` (CH Store) → its POS Profile's ``cost_center``
3. ``warehouse`` → the CH Store that owns it → its POS Profile's ``cost_center``
4. ``Company.cost_center`` — the historical default, kept so a caller never
   ends up with a blank Cost Center on a document that requires one.

Every lookup is company-guarded: a Cost Center from another company would fail
ERPNext's own validation, so a cross-company hit is discarded rather than
returned.
"""

from __future__ import annotations

import frappe


def _company_of(cost_center: str | None) -> str | None:
	if not cost_center:
		return None
	return frappe.db.get_value("Cost Center", cost_center, "company")


def _valid_for(cost_center: str | None, company: str | None) -> str | None:
	"""Return cost_center only when it exists and belongs to ``company``."""
	if not cost_center:
		return None
	cc_company = _company_of(cost_center)
	if not cc_company:
		return None
	if company and cc_company != company:
		return None
	return cost_center


def _cost_center_of_profile(pos_profile: str | None, company: str | None) -> str | None:
	if not pos_profile:
		return None
	return _valid_for(
		frappe.db.get_value("POS Profile", pos_profile, "cost_center"), company
	)


def _store_row(filters: dict) -> dict | None:
	if not frappe.db.table_exists("CH Store"):
		return None
	return frappe.db.get_value(
		"CH Store",
		filters,
		["name", "store_code", "company", "warehouse", "pos_profile"],
		as_dict=True,
	)


def _cost_center_of_store(store_row, company: str | None) -> str | None:
	if not store_row:
		return None
	store_company = company or store_row.company
	found = _cost_center_of_profile(store_row.pos_profile, store_company)
	if found:
		return found
	# A Cost Center is provisioned even before a store receives its warehouse
	# or POS Profile. This fallback keeps non-POS flows independent of cashier
	# setup while still resolving only the deterministic managed leaf.
	label = f"POS - {store_row.store_code or store_row.name}"
	return _valid_for(
		frappe.db.get_value(
			"Cost Center",
			{"company": store_row.company, "cost_center_name": label},
			"name",
		),
		store_company,
	)


def _warehouse_to_store(warehouse: str, company: str | None) -> dict | None:
	"""Find the CH Store that owns ``warehouse``.

	A store owns a small warehouse tree — a group ``<STORE> - <ABBR>`` holding
	Sellable / Damaged / Demo / Buyback leaves — but ``CH Store.warehouse``
	points at the **Sellable leaf**, not the group. So an exact match only works
	for that one leaf, and walking up only ever reaches the group, which no store
	names. Each level therefore has to check the ancestor's *children* too, which
	is what resolves a Buyback/Damaged/Demo leaf back to its store.
	"""

	def _match(filters: dict) -> dict | None:
		row = _store_row(filters)
		if row and (not company or row.company == company):
			return row
		return None

	# New and backfilled warehouse trees carry their owning store explicitly.
	if frappe.db.has_column("Warehouse", "ch_store"):
		store_name = frappe.db.get_value("Warehouse", warehouse, "ch_store")
		if store_name:
			found = _match({"name": store_name})
			if found:
				return found

	# 1. The store's own nominated warehouse.
	found = _match({"warehouse": warehouse})
	if found:
		return found

	# 2. ``warehouse`` may itself be the store group (e.g. "GG-KELLYS - BM"),
	#    whose Sellable child is what the store actually names.
	own_children = frappe.get_all(
		"Warehouse", filters={"parent_warehouse": warehouse}, pluck="name", limit_page_length=0
	)
	if own_children:
		found = _match({"warehouse": ("in", own_children)})
		if found:
			return found

	# 3. Walk up; at each level try the ancestor itself, then its children
	#    (siblings of the warehouse we came from).
	seen = 0
	current = warehouse
	while current and seen < 6:  # depth guard; these trees are 3-4 deep
		parent = frappe.db.get_value("Warehouse", current, "parent_warehouse")
		if not parent:
			break
		found = _match({"warehouse": parent})
		if found:
			return found
		siblings = frappe.get_all(
			"Warehouse",
			filters={"parent_warehouse": parent},
			pluck="name",
			limit_page_length=0,
		)
		if siblings:
			found = _match({"warehouse": ("in", siblings)})
			if found:
				return found
		current = parent
		seen += 1
	return None


def resolve_cost_center(
	company: str | None = None,
	*,
	warehouse: str | None = None,
	store: str | None = None,
	pos_profile: str | None = None,
	fallback_to_company: bool = True,
) -> str | None:
	"""Return the Cost Center a document should post against.

	Pass whatever context the caller has; the most specific signal wins. Returns
	``None`` only when nothing resolves and ``fallback_to_company`` is False.
	"""
	# 1. Explicit POS Profile
	found = _cost_center_of_profile(pos_profile, company)
	if found:
		return found

	# 2. Explicit store
	if store:
		row = _store_row({"name": store}) or _store_row({"store_code": store})
		if row and (not company or row.company == company):
			found = _cost_center_of_store(row, company or row.company)
			if found:
				return found

	# 3. Warehouse → owning store
	if warehouse:
		row = _warehouse_to_store(warehouse, company)
		if row:
			found = _cost_center_of_store(row, company or row.company)
			if found:
				return found
		if not company:
			company = frappe.db.get_value("Warehouse", warehouse, "company")

	# 4. Company default — what every non-POS path used to get implicitly.
	if fallback_to_company and company:
		return _valid_for(
			frappe.db.get_value("Company", company, "cost_center"), company
		)
	return None


def apply_cost_center(
	doc,
	*,
	warehouse: str | None = None,
	store: str | None = None,
	pos_profile: str | None = None,
) -> str | None:
	"""Set ``doc.cost_center`` (and item rows, when present) if not already set.

	Item-level Cost Centers are what actually reach the P&L: ERPNext posts
	income/expense GL from the item row's ``cost_center``, falling back to the
	parent only when the row is blank. Setting the header alone leaves the
	attribution incomplete.
	"""
	company = doc.get("company")
	cost_center = resolve_cost_center(
		company, warehouse=warehouse, store=store, pos_profile=pos_profile
	)
	if not cost_center:
		return None

	company_default = frappe.db.get_value("Company", company, "cost_center") if company else None

	def may_default(row) -> bool:
		return not row.get("cost_center") or row.get("cost_center") == company_default

	if doc.meta.has_field("cost_center") and may_default(doc):
		doc.cost_center = cost_center

	# "items" covers Sales/Purchase Invoice, Stock Entry, Delivery Note, Purchase
	# Receipt; "accounts" covers Journal Entry. Both carry their own cost_center.
	for table in ("items", "accounts", "taxes", "deductions", "expenses"):
		for row in doc.get(table) or []:
			if row.meta.has_field("cost_center") and may_default(row):
				row.cost_center = cost_center

	return cost_center


def _unique(values) -> str | None:
	values = {value for value in values if value}
	return next(iter(values)) if len(values) == 1 else None


def _warehouse_cost_center(company, warehouse):
	if not warehouse:
		return None
	return resolve_cost_center(company, warehouse=warehouse, fallback_to_company=False)


def _reference_cost_center(reference_type, reference_name, company):
	if not reference_type or not reference_name or not frappe.db.exists(
		reference_type, reference_name
	):
		return None
	meta = frappe.get_meta(reference_type)
	company_default = (
		frappe.db.get_value("Company", company, "cost_center") if company else None
	)
	if meta.has_field("cost_center"):
		direct = _valid_for(
			frappe.db.get_value(reference_type, reference_name, "cost_center"), company
		)
		if direct and direct != company_default:
			return direct

	# Imported/legacy documents often have a blank or Company-default header
	# but a correctly attributed item row. Treat one unique non-default child
	# value as authoritative; mixed-store references deliberately resolve None.
	items_field = meta.get_field("items")
	if items_field and items_field.options:
		values = frappe.get_all(
			items_field.options,
			filters={"parent": reference_name, "cost_center": ("is", "set")},
			pluck="cost_center",
			limit_page_length=0,
		)
		return _unique(
			_valid_for(value, company)
			for value in values
			if value != company_default
		)
	return None


def resolve_reference_cost_center(reference_type, reference_name, company=None):
	"""Return one non-default Cost Center carried by a source document."""
	return _reference_cost_center(reference_type, reference_name, company)


def _row_warehouse_fields(doctype):
	if doctype in ("Sales Invoice", "Delivery Note", "Sales Order"):
		return ("warehouse", "s_warehouse")
	if doctype in ("Purchase Invoice", "Purchase Receipt", "Purchase Order"):
		return ("warehouse", "t_warehouse", "rejected_warehouse")
	return (
		"warehouse",
		"s_warehouse",
		"t_warehouse",
		"from_warehouse",
		"target_warehouse",
	)


def apply_document_store_cost_center(doc, method=None):
    """Default store Cost Centers on operational/accounting documents.

    Only an unambiguous store signal is applied. Blank and Company-default
    values may be replaced; any intentional non-default assignment is kept.
    Multi-store transfers are routed per row where possible and never forced
    to a misleading single header Cost Center.
    """
    company = doc.get("company")
    if not company:
        return None
    company_default = frappe.db.get_value("Company", company, "cost_center")

    def may_default(row):
        return not row.get("cost_center") or row.get("cost_center") == company_default

    candidates = []

    pos_profile = doc.get("pos_profile") if doc.meta.has_field("pos_profile") else None
    if pos_profile:
        candidates.append(
            resolve_cost_center(
                company, pos_profile=pos_profile, fallback_to_company=False
            )
        )

    if doc.meta.has_field("custom_ch_pos_session") and doc.get("custom_ch_pos_session"):
        session = frappe.db.get_value(
            "CH POS Session",
            doc.get("custom_ch_pos_session"),
            ["store", "pos_profile"],
            as_dict=True,
        )
        if session:
            candidates.append(
                resolve_cost_center(
                    company,
                    store=session.store,
                    pos_profile=session.pos_profile,
                    fallback_to_company=False,
                )
            )

    for fieldname in (
        "ch_store",
        "store",
        "custom_source_store",
        "custom_target_store",
    ):
        if doc.meta.has_field(fieldname) and doc.get(fieldname):
            candidates.append(
                resolve_cost_center(
                    company, store=doc.get(fieldname), fallback_to_company=False
                )
            )

    for fieldname in (
        "warehouse",
        "set_warehouse",
        "from_warehouse",
        "to_warehouse",
        "set_from_warehouse",
        "set_target_warehouse",
        "rejected_warehouse",
    ):
        if doc.meta.has_field(fieldname) and doc.get(fieldname):
            candidates.append(_warehouse_cost_center(company, doc.get(fieldname)))

    row_fields = _row_warehouse_fields(doc.doctype)
    for row in doc.get("items") or []:
        row_candidates = [
            _warehouse_cost_center(company, row.get(fieldname))
            for fieldname in row_fields
            if row.meta.has_field(fieldname) and row.get(fieldname)
        ]
        row_cc = _unique(row_candidates)
        if row_cc:
            candidates.append(row_cc)
            if row.meta.has_field("cost_center") and may_default(row):
                row.cost_center = row_cc

    # Payment Entries inherit a store only when every allocated reference
    # belongs to the same store. Cross-store settlements remain unassigned.
    for row in doc.get("references") or []:
        candidates.append(
            _reference_cost_center(
                row.get("reference_doctype"), row.get("reference_name"), company
            )
        )

    # Journal Entry rows can carry their own reference. Route each referenced
    # row independently, then use a header-wide value only when all agree.
    for row in doc.get("accounts") or []:
        row_cc = _reference_cost_center(
            row.get("reference_type"), row.get("reference_name"), company
        )
        if row_cc:
            candidates.append(row_cc)
            if row.meta.has_field("cost_center") and may_default(row):
                row.cost_center = row_cc

    header_cc = _unique(candidates)
    if not header_cc:
        return None

    if doc.meta.has_field("cost_center") and may_default(doc):
        doc.cost_center = header_cc

    for table in ("items", "accounts", "taxes", "deductions", "expenses"):
        for row in doc.get(table) or []:
            if row.meta.has_field("cost_center") and may_default(row):
                row.cost_center = header_cc
    return header_cc
