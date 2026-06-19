# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
GST Item-Tax-Template auto-provisioner.

CH Sub Category authoritatively owns the (`hsn_code`, `gst_rate`, `gst_treatment`)
tuple for every Item created under it. This module turns that single source of
truth into the actual artefacts the rest of the stack (ERPNext + India
Compliance + ch_erp15 transaction validators) needs:

    1. **Per-company `Item Tax Template`** — one for every distinct
       (`company`, `gst_rate`, `gst_treatment`) tuple. These are the rows
       `gst_utils.get_item_gst_rates()` resolves at PO/PR/PI/SI validate time.

    2. **`GST HSN Code.taxes` rows** referencing the templates above. India
       Compliance's `set_taxes_from_hsn_code(doc)` (Item.validate hook) copies
       these rows into `Item.taxes` when the Item is saved.

    3. **`Item.taxes` propagation** — handled by `_cascade_tax_to_items()` on
       CH Sub Category by re-reading the freshly-wired `GST HSN Code.taxes`.

The recipe for building an `Item Tax Template` mirrors India Compliance's own
internal helper (`gst_india/overrides/test_item_tax_template.py:create_item_tax_template`),
promoted here to production code so production transactions get the same shape
of tax rows that the IC validators expect.

Idempotent and side-effect-bounded:
    - Re-running on the same (sub_category, company) is a no-op.
    - Skips companies whose `country != "India"`.
    - Skips companies that have not yet been bootstrapped with GST accounts
      (no `GST Settings.gst_accounts` row); logs a single info-level message
      and returns. This lets fresh sites finish company creation before any
      Sub Category save tries to wire templates.
    - Never throws on absence of accounts — only on shape contradictions
      (e.g. India Compliance's own validator rejecting our rates).

Direct call sites:
    - CHSubCategory.validate → after HSN/gst_rate populate, before linkage cascade
    - CHSubCategory.on_update → on `gst_rate` value change, re-cascades to items
    - bench command (idempotent backfill for existing data)
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from india_compliance.gst_india.overrides.transaction import get_valid_accounts
from india_compliance.gst_india.utils import get_gst_accounts_by_type


# Treatments that imply gst_rate==0 with NO tax accounts on the template.
# See india_compliance.gst_india.doctype.item_tax_template.item_tax_template.validate_tax_rates
_ZERO_RATE_TREATMENTS = {"Nil-Rated", "Exempt", "Non-GST"}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def ensure_item_tax_template(
	company: str,
	gst_rate: float,
	gst_treatment: str = "Taxable",
) -> str | None:
	"""Return the name of the canonical Item Tax Template for
	(company, gst_rate, gst_treatment), creating it if missing.

	Returns ``None`` (and logs once) when the company is missing GST account
	configuration — the caller MUST tolerate this and continue.

	Args:
		company:        Company name. Must exist; must be country=India.
		gst_rate:       Numeric GST rate (e.g. ``18`` for 18%). For
		                ``Nil-Rated``/``Exempt``/``Non-GST`` this is forced to 0.
		gst_treatment:  GST treatment per India Compliance: ``Taxable``,
		                ``Nil-Rated``, ``Exempt``, ``Non-GST``, ``Zero-Rated``.

	Idempotent.
	"""
	if not company:
		return None

	gst_treatment = gst_treatment or "Taxable"
	if gst_treatment in _ZERO_RATE_TREATMENTS:
		gst_rate = 0
	gst_rate = flt(gst_rate)

	# Skip non-India companies entirely.
	country = frappe.db.get_value("Company", company, "country")
	if country and country.lower() != "india":
		return None

	# Look for an existing template for (company, gst_rate, gst_treatment).
	# Prefer one that we created (title pattern below), but accept any valid
	# match so we don't duplicate when an admin pre-created theirs.
	existing = frappe.db.get_value(
		"Item Tax Template",
		{
			"company": company,
			"gst_rate": gst_rate,
			"gst_treatment": gst_treatment,
			"disabled": 0,
		},
		"name",
	)
	if existing:
		return existing

	# Build the template using India Compliance's account discovery.
	__, intra_state_accounts, inter_state_accounts = get_valid_accounts(
		company, for_sales=True, for_purchase=True, throw=False
	)

	if not intra_state_accounts and not inter_state_accounts:
		# Company is not GST-configured yet (no GST Settings row).
		frappe.logger("ch_item_master").info(
			f"ensure_item_tax_template: skipping {company!r} — "
			f"no GST accounts wired in GST Settings (gst_rate={gst_rate}, "
			f"treatment={gst_treatment})."
		)
		return None

	# RCM accounts go in negative for sales-side templates per IC convention.
	rcm_accounts = set(
		(get_gst_accounts_by_type(company, "Sales Reverse Charge", throw=False) or {}).values()
	)

	abbr = frappe.db.get_value("Company", company, "abbr") or company
	# Compact title: humans see this in dropdowns. Fixed pattern so future runs
	# can recognise (and reuse) what we created.
	title_rate = int(gst_rate) if float(gst_rate).is_integer() else gst_rate
	if gst_treatment == "Taxable":
		title = f"GST {title_rate}% - {abbr}"
	else:
		title = f"GST {gst_treatment} - {abbr}"

	# Disambiguate if a stale-but-disabled template with the same title exists.
	if frappe.db.exists("Item Tax Template", title):
		# Caller asked for a clean lookup — return whatever exists at that
		# exact name so we never insert two with conflicting titles.
		return title

	doc = frappe.new_doc("Item Tax Template")
	doc.update(
		{
			"company": company,
			"title": title,
			"gst_treatment": gst_treatment,
			"gst_rate": gst_rate,
		}
	)

	if gst_treatment in _ZERO_RATE_TREATMENTS:
		# India Compliance forbids tax rows on these treatments.
		pass
	else:
		# CGST + SGST go at half the headline rate; IGST at full rate.
		seen = set()
		for account in intra_state_accounts:
			if not account or account in seen:
				continue
			seen.add(account)
			tax_rate = flt(gst_rate) / 2.0
			if account in rcm_accounts:
				tax_rate = -tax_rate
			doc.append("taxes", {"tax_type": account, "tax_rate": tax_rate})

		for account in inter_state_accounts:
			if not account or account in seen:
				continue
			seen.add(account)
			tax_rate = flt(gst_rate)
			if account in rcm_accounts:
				tax_rate = -tax_rate
			doc.append("taxes", {"tax_type": account, "tax_rate": tax_rate})

	doc.flags.ignore_permissions = True
	# `insert(ignore_if_duplicate=True)` shields against rare race conditions
	# where two parallel saves on different sub-categories try to mint the
	# same template — second one silently no-ops.
	doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
	return doc.name


def ensure_templates_for_subcategory(sub_category) -> dict:
	"""Provision Item Tax Templates for every India company AND wire them into
	the `GST HSN Code.taxes` table so India Compliance's standard
	`set_taxes_from_hsn_code()` Item-validate hook propagates them naturally.

	Args:
		sub_category: A `CHSubCategory` Document instance OR a Sub Category name.

	Returns:
		dict — ``{company: item_tax_template_name_or_None, ...}`` for diagnostics.
	"""
	if isinstance(sub_category, str):
		sub_category = frappe.get_doc("CH Sub Category", sub_category)

	if not sub_category.hsn_code:
		return {}

	gst_rate = flt(sub_category.get("gst_rate") or 0)
	gst_treatment = sub_category.get("gst_treatment") or "Taxable"

	# Force-treatment alignment: a 0-rate Sub Category configured as "Taxable"
	# would fail India Compliance's own validator. Snap to "Nil-Rated" so
	# the template can be created without mutating user intent at the rate.
	if gst_treatment == "Taxable" and gst_rate == 0:
		gst_treatment = "Nil-Rated"

	companies = _india_companies()
	result: dict[str, str | None] = {}

	for company in companies:
		template = ensure_item_tax_template(company, gst_rate, gst_treatment)
		result[company] = template
		if template:
			_ensure_hsn_tax_row(sub_category.hsn_code, template)

	return result


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────


def _india_companies() -> list[str]:
	"""All non-test India companies on this site, ordered for stable diffs."""
	rows = frappe.get_all(
		"Company",
		filters={"country": "India"},
		pluck="name",
		order_by="name asc",
	)
	# Skip Frappe core test companies — they're never used in real flows
	# and IC's GST Settings is intentionally unwired for them.
	return [c for c in rows if not c.startswith("_Test")]


def _ensure_hsn_tax_row(hsn_code: str, item_tax_template: str) -> None:
	"""Append an `Item Tax` row to `GST HSN Code.taxes` for the given template
	if (and only if) the HSN code does not already point at it.

	Avoids `frappe.get_doc(...).save()` on GST HSN Code because that would
	fire India Compliance's own validators repeatedly on every Sub Category
	save. We directly manipulate the child rows: idempotent, fast, side-
	effect-free for callers that don't care about GST HSN Code.modified.
	"""
	already = frappe.db.exists(
		"Item Tax",
		{
			"parenttype": "GST HSN Code",
			"parent": hsn_code,
			"item_tax_template": item_tax_template,
		},
	)
	if already:
		return

	parent = frappe.get_doc("GST HSN Code", hsn_code)
	parent.append("taxes", {"item_tax_template": item_tax_template})
	parent.flags.ignore_permissions = True
	parent.flags.ignore_validate_update_after_submit = True
	# GST HSN Code is non-submittable — save() is the canonical path to
	# persist the new child row with proper docname and idx.
	parent.save(ignore_permissions=True)


# ─────────────────────────────────────────────────────────────────────────────
# CLI / bench command
# ─────────────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def backfill_all_subcategories() -> dict:
	"""Idempotent backfill: walk every Sub Category, ensure templates +
	HSN linkage. Safe to run repeatedly. Whitelisted so it can be called
	from `bench execute` or admin UI.

	Returns:
		dict — ``{"total": N, "linked": M, "skipped": K}``
	"""
	total = linked = skipped = 0
	names = frappe.get_all("CH Sub Category", pluck="name")
	for name in names:
		total += 1
		try:
			out = ensure_templates_for_subcategory(name)
			if any(out.values()):
				linked += 1
			else:
				skipped += 1
		except Exception:
			skipped += 1
			frappe.log_error(
				title=f"GST template backfill failed for {name}",
				message=frappe.get_traceback(),
			)
	frappe.db.commit()
	return {"total": total, "linked": linked, "skipped": skipped}
