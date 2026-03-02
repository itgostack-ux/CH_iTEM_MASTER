# Copyright (c) 2026, GoStack and contributors
# Ready Reckoner API — backend for the CH Ready Reckoner page
#
# Rule Engine (get_active_price):
#   For Item + Channel + Date:
#     1. Find CH Item Price records that are Active/Scheduled and date-range covers the date
#     2. Pick the one with latest effective_from
#     3. For offers: filter by date+channel, sort by priority desc
#        - If Price Override exists → final = override price
#        - Else collect stackable discounts + highest non-stackable

import re

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, get_datetime, now_datetime
from frappe.utils.xlsxutils import make_xlsx


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: affects_price spec grouping
# ─────────────────────────────────────────────────────────────────────────────

def _get_non_price_specs():
    """Return set of Item Attribute names that are variant specs but do NOT affect price.

    These are specs with is_variant=1 AND affects_price=0 in any CH Sub Category Spec row.
    Built as a global set — the same attribute (e.g. "Colour") may affect price in one
    sub-category but not another, so we index per sub-category.
    """
    rows = frappe.get_all(
        "CH Sub Category Spec",
        filters={"is_variant": 1, "affects_price": 0},
        fields=["parent as sub_category", "spec"],
    )
    # Return dict: sub_category → set of non-price spec names
    result: dict = {}
    for r in rows:
        result.setdefault(r.sub_category, set()).add(r.spec)
    return result


def _build_price_group_key(item, variant_attributes, non_price_specs_map):
    """Build a grouping key for an item based on price-affecting variant attributes.

    Items that share the same key differ only in non-price specs (like Color) and should
    be displayed as a single row in the Ready Reckoner.

    Returns: (model, price_spec_1_value, price_spec_2_value, ...)
    """
    sub_cat = item.get("ch_sub_category") or ""
    model = item.get("ch_model") or ""
    non_price_specs = non_price_specs_map.get(sub_cat, set())

    if not non_price_specs:
        # No non-price specs defined — each item is its own group
        return item["item_code"]

    # Get variant attributes for this item, keep only price-affecting ones
    attrs = variant_attributes.get(item["item_code"], {})
    price_parts = []
    for attr_name in sorted(attrs.keys()):
        if attr_name not in non_price_specs:
            price_parts.append(f"{attr_name}={attrs[attr_name]}")

    return f"{model}||{'|'.join(price_parts)}" if price_parts else model or item["item_code"]


# ─────────────────────────────────────────────────────────────────────────────
# Main Ready Reckoner grid
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_ready_reckoner_data(
    category=None,
    sub_category=None,
    brand=None,
    model=None,
    item_search=None,
    channel=None,
    as_of_date=None,
    tag_filter=None,
    price_status=None,
    company=None,
    page_length=100,
    page=1,
    group_by_price_specs=0,
):
    """Return the Ready Reckoner grid data.

    When group_by_price_specs=1, items that differ only in non-price specs
    (e.g. Color for phones) are collapsed into a single row showing the
    representative item, with a 'variant_count' field indicating how many
    color/non-price variants exist.
    """
    frappe.only_for(["System Manager", "CH Master Manager", "CH Price Manager",
                     "CH Offer Manager", "CH Warranty Manager", "CH Viewer", "Stock User"])

    as_of = getdate(as_of_date) if as_of_date else getdate(nowdate())

    # Company filter: include records where company matches OR company is blank (all companies)
    _company_filter = ["in", [company, "", None]] if company else None
    offset = (int(page) - 1) * int(page_length)

    # ── 1. Fetch items matching filters ──────────────────────────────────────
    item_filters = {"disabled": 0}
    if category:
        item_filters["ch_category"] = category
    if sub_category:
        item_filters["ch_sub_category"] = sub_category
    if brand:
        item_filters["brand"] = brand
    if model:
        item_filters["ch_model"] = model
    if item_search:
        # handled with OR clause below
        pass

    items = frappe.get_all(
        "Item",
        filters=item_filters,
        or_filters=(
            [
                ["name", "like", f"%{item_search}%"],
                ["item_name", "like", f"%{item_search}%"],
            ]
            if item_search
            else None
        ),
        fields=[
            "name as item_code",
            "item_name",
            "item_group",
            "brand",
            "ch_category",
            "ch_sub_category",
            "ch_model",
        ],
        limit_start=offset,
        limit_page_length=int(page_length),
        order_by="item_name asc",
    )

    if not items:
        return {"items": [], "channels": [], "total": 0}

    item_codes = [i["item_code"] for i in items]

    # ── 1b. Group by price-affecting specs (optional) ────────────────────────
    # When enabled, items differing only in non-price specs (e.g. Color for
    # phones) are collapsed into a single representative row.
    group_mode = int(group_by_price_specs)
    grouped_items = items  # default: no grouping
    all_item_codes = list(item_codes)  # all codes for price/offer lookups

    if group_mode:
        non_price_map = _get_non_price_specs()
        if non_price_map:
            # Fetch variant attributes for all items in one query
            va_rows = frappe.db.sql("""
                SELECT parent, attribute, attribute_value
                FROM `tabItem Variant Attribute`
                WHERE parent IN %(items)s AND attribute_value IS NOT NULL
            """, {"items": item_codes}, as_dict=True)

            variant_attrs: dict = {}
            for va in va_rows:
                variant_attrs.setdefault(va.parent, {})[va.attribute] = va.attribute_value

            # Group items by price-group key
            groups: dict = {}
            for item in items:
                key = _build_price_group_key(item, variant_attrs, non_price_map)
                if key not in groups:
                    groups[key] = {"representative": item, "members": []}
                groups[key]["members"].append(item["item_code"])

            # Build grouped items list: representative + variant_count
            grouped_items = []
            for key, grp in groups.items():
                row = dict(grp["representative"])
                row["variant_count"] = len(grp["members"])
                row["variant_item_codes"] = grp["members"]

                # Strip non-price spec values from the display name
                sub_cat = row.get("ch_sub_category") or ""
                np_specs = non_price_map.get(sub_cat, set())
                if np_specs and row.get("item_name"):
                    attrs_for_item = variant_attrs.get(row["item_code"], {})
                    for spec_name in np_specs:
                        val = attrs_for_item.get(spec_name, "")
                        if val and val in row["item_name"]:
                            row["item_name"] = row["item_name"].replace(val, "").strip()
                    # Clean up any double spaces left after stripping
                    row["item_name"] = re.sub(r"\s{2,}", " ", row["item_name"]).strip()

                grouped_items.append(row)

            # all_item_codes stays the same — we need prices for any member
        else:
            for item in items:
                item["variant_count"] = 1
    else:
        for item in items:
            item["variant_count"] = 1

    # ── 2. Get all channels (with price_list for later use) ──────────────
    channels_qs = frappe.get_all(
        "CH Price Channel",
        filters={"disabled": 0, **({"channel_name": channel} if channel else {})},
        fields=["name as channel_name", "price_list"],
        order_by="channel_name",
    )
    channel_names = [c["channel_name"] for c in channels_qs]
    # Optimization: Pre-fetch channel-to-price-list mapping to avoid N+1 queries
    channel_price_list_map = {c["channel_name"]: c.get("price_list") for c in channels_qs}

    # ── 3. Fetch active prices for those items ────────────────────────────────
    _price_filters = {
        "item_code": ("in", item_codes),
        "status": ("in", ["Active", "Scheduled"]),
        "effective_from": ("<=", str(as_of)),
    }
    if _company_filter:
        _price_filters["company"] = _company_filter

    price_records = frappe.get_all(
        "CH Item Price",
        filters=_price_filters,
        fields=[
            "item_code", "channel", "mrp", "mop", "selling_price",
            "effective_from", "effective_to",
            "status", "name as price_name",
        ],
        order_by="item_code, channel, effective_from desc",
    )

    # Index: item_code → channel → best price (latest effective_from per channel)
    price_index: dict = {}
    for pr in price_records:
        # Skip if effective_to has passed
        if pr.effective_to and getdate(pr.effective_to) < as_of:
            continue
        if price_status and pr.status != price_status:
            continue
        key = (pr.item_code, pr.channel)
        if key not in price_index:          # already sorted desc effective_from
            price_index[key] = pr

    # ── 4. Fetch active offers summary ────────────────────────────────────────
    # Use the requested as_of date (not now) so historical views are correct
    as_of_str = str(as_of)  # YYYY-MM-DD
    _offer_filters = {
        "item_code": ("in", item_codes),
        "status": ("in", ["Active", "Scheduled"]),
        "approval_status": "Approved",
        "start_date": ("<=", as_of_str),
        "end_date": (">=", as_of_str),
    }
    if _company_filter:
        _offer_filters["company"] = _company_filter

    offer_records = frappe.get_all(
        "CH Item Offer",
        filters=_offer_filters,
        fields=[
            "item_code", "offer_type", "value_type", "value",
            "priority", "stackable", "channel",
        ],
    )

    # Index: item_code → list of active offers
    offer_index: dict = {}
    for of in offer_records:
        offer_index.setdefault(of.item_code, []).append(of)

    # ── 5. Fetch active tags ──────────────────────────────────────────────────
    tag_filters = {
        "item_code": ("in", item_codes),
        "status": "Active",
    }
    if _company_filter:
        tag_filters["company"] = _company_filter
    if tag_filter:
        tag_filters["tag"] = tag_filter

    tag_records = frappe.get_all(
        "CH Item Commercial Tag",
        filters=tag_filters,
        fields=["item_code", "tag"],
    )
    tag_index: dict = {}
    for t in tag_records:
        tag_index.setdefault(t.item_code, []).append(t.tag)

    # ── 6. Merge into rows ────────────────────────────────────────────────────
    rows = []
    for item in grouped_items:
        ic = item["item_code"]
        row = dict(item)
        member_codes = item.get("variant_item_codes", [ic])

        # Prices per channel — for grouped items, use price from any member
        # (they share the same price since non-price specs don't affect pricing)
        for ch in channel_names:
            pr = price_index.get((ic, ch))
            if not pr and group_mode:
                for mc in member_codes:
                    pr = price_index.get((mc, ch))
                    if pr:
                        break
            if pr:
                row[f"{ch}__mrp"]          = pr.mrp
                row[f"{ch}__mop"]          = pr.mop
                row[f"{ch}__selling_price"] = pr.selling_price
                row[f"{ch}__status"]        = pr.status
                row[f"{ch}__price_name"]    = pr.price_name
            else:
                row[f"{ch}__mrp"]          = None
                row[f"{ch}__mop"]          = None
                row[f"{ch}__selling_price"] = None
                row[f"{ch}__status"]        = "—"
                row[f"{ch}__price_name"]    = None

        # Offers summary — aggregate across all member items
        all_member_offers = []
        for mc in member_codes:
            all_member_offers.extend(offer_index.get(mc, []))
        active_offer = _compute_best_offer(all_member_offers)
        row["offer_count"]       = len(all_member_offers)
        row["active_offer_price"] = active_offer.get("price")
        row["active_offer_label"] = active_offer.get("label")
        row["has_bank_offer"]     = any(o.offer_type == "Bank Offer" for o in all_member_offers)
        row["has_brand_offer"]    = any(o.offer_type == "Brand Offer" for o in all_member_offers)

        # Tags — union across members
        all_tags = set()
        for mc in member_codes:
            all_tags.update(tag_index.get(mc, []))
        row["tags"] = ", ".join(sorted(all_tags))

        # Clean up internal field before sending to frontend
        row.pop("variant_item_codes", None)

        rows.append(row)

    # frappe.db.count() does not support or_filters, so handle item_search separately
    if item_search:
        total = len(frappe.get_all(
            "Item",
            filters=item_filters,
            or_filters=[
                ["name", "like", f"%{item_search}%"],
                ["item_name", "like", f"%{item_search}%"],
            ],
            pluck="name",
        ))
    else:
        total = frappe.db.count("Item", filters=item_filters)

    return {
        "items": rows,
        "channels": channel_names,
        "total": total,
        "page": int(page),
        "page_length": int(page_length),
    }


def _compute_best_offer(offers):
    """Apply priority + stackable logic to compute the best offer label."""
    if not offers:
        return {}

    # Sort by priority desc
    sorted_offers = sorted(offers, key=lambda o: -(o.priority or 1))

    # Check for Price Override (highest priority wins)
    for o in sorted_offers:
        if o.offer_type == "Price Override" or o.value_type == "Price Override":
            return {
                "price": o.value,
                "label": f"Override ₹{o.value:,.0f}",
            }

    # Collect stackable discounts + first non-stackable
    total_pct = 0
    total_amt = 0
    seen_non_stackable = False
    for o in sorted_offers:
        if o.stackable:
            if o.value_type == "Percentage":
                total_pct += o.value
            else:
                total_amt += o.value
        elif not seen_non_stackable:
            if o.value_type == "Percentage":
                total_pct += o.value
            else:
                total_amt += o.value
            seen_non_stackable = True

    parts = []
    if total_pct:
        parts.append(f"{total_pct:.1f}% off")
    if total_amt:
        parts.append(f"₹{total_amt:,.0f} off")

    return {"price": None, "label": " + ".join(parts) if parts else ""}


# ─────────────────────────────────────────────────────────────────────────────
# Item price detail (for the drawer / side panel)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_item_price_detail(item_code, company=None):
    """Return full price + offer + tag detail for the side drawer."""
    frappe.only_for(["System Manager", "CH Master Manager", "CH Price Manager",
                     "CH Offer Manager", "CH Warranty Manager", "CH Viewer", "Stock User"])

    _detail_company = ["in", [company, "", None]] if company else None

    _price_det_filters = {"item_code": item_code}
    if _detail_company:
        _price_det_filters["company"] = _detail_company

    prices = frappe.get_all(
        "CH Item Price",
        filters=_price_det_filters,
        fields=[
            "name", "channel", "mrp", "mop", "selling_price",
            "effective_from", "effective_to",
            "status", "approved_by", "approved_at", "notes",
            "modified_by", "modified", "erp_item_price",
        ],
        order_by="effective_from desc",
        limit=50,
    )

    # Enrich each price with ERPNext Price List and ERP sync status
    # Optimization: Bulk fetch all channel price lists to avoid N+1 queries
    channel_ids = list(set(p.get("channel") for p in prices if p.get("channel")))
    _ch_channel_pl = {}
    if channel_ids:
        channel_data = frappe.get_all(
            "CH Price Channel",
            filters={"name": ("in", channel_ids)},
            fields=["name", "price_list"]
        )
        _ch_channel_pl = {c.name: c.price_list or "" for c in channel_data}
    
    for p in prices:
        ch = p.get("channel")
        p["price_list"] = _ch_channel_pl.get(ch, "")
        # Verify the ERP Item Price still exists and is consistent
        erp_ip = p.get("erp_item_price")
        if erp_ip and frappe.db.exists("Item Price", erp_ip):
            p["erp_synced"] = True
            p["erp_item_price_rate"] = frappe.db.get_value("Item Price", erp_ip, "price_list_rate")
        else:
            p["erp_synced"] = bool(erp_ip)

    _offer_det_filters = {"item_code": item_code}
    if _detail_company:
        _offer_det_filters["company"] = _detail_company

    offers = frappe.get_all(
        "CH Item Offer",
        filters=_offer_det_filters,
        fields=[
            "name", "offer_name", "offer_type", "value_type", "value",
            "channel", "priority", "stackable", "start_date", "end_date",
            "status", "approval_status", "bank_name", "card_type",
            "min_bill_amount", "payment_mode", "notes", "erp_pricing_rule",
            "offer_level", "apply_on", "target_item_group", "target_brand",
        ],
        order_by="start_date desc",
        limit=50,
    )

    _tag_det_filters = {"item_code": item_code}
    if _detail_company:
        _tag_det_filters["company"] = _detail_company

    tags = frappe.get_all(
        "CH Item Commercial Tag",
        filters=_tag_det_filters,
        fields=["name", "tag", "effective_from", "effective_to", "status", "reason"],
        order_by="creation desc",
        limit=20,
    )

    # Fetch history via docname references (avoids expensive LIKE scan on Version.data)
    price_names = [p.name for p in prices]
    offer_names = [o.name for o in offers]
    tag_names = [t.name for t in tags]
    ref_names = price_names + offer_names + tag_names

    history = []
    if ref_names:
        history = frappe.get_all(
            "Version",
            filters={
                "ref_doctype": ("in", ["CH Item Price", "CH Item Offer", "CH Item Commercial Tag"]),
                "docname": ("in", ref_names),
            },
            fields=["name", "ref_doctype", "docname", "creation", "owner"],
            order_by="creation desc",
            limit=30,
        )

    return {
        "prices": prices,
        "offers": offers,
        "tags": tags,
        "history": history,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rule engine — resolve final price for a given item+channel+date
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_active_price(item_code, channel, as_of_date=None):
    """Return the final selling price for item+channel on as_of_date.

    Output:
        base_price    — active CH Item Price record values
        final_price   — after applying best offer
        offer_label   — human-readable offer summary
        has_bank_offer, has_brand_offer
    """
    as_of = getdate(as_of_date) if as_of_date else getdate(nowdate())

    # Base price
    prices = frappe.get_all(
        "CH Item Price",
        filters={
            "item_code": item_code,
            "channel": channel,
            "status": ("in", ["Active", "Scheduled"]),
            "effective_from": ("<=", str(as_of)),
        },
        fields=[
            "name", "mrp", "mop", "selling_price",
            "effective_from", "effective_to",
        ],
        order_by="effective_from desc",
        limit=1,
    )

    if not prices:
        return {
            "found": False,
            "message": _("No active price found for item {0} in channel {1} on {2}").format(
                item_code, channel, as_of
            )
        }

    bp = prices[0]
    # Verify effective_to
    if bp.effective_to and getdate(bp.effective_to) < as_of:
        return {"found": False}

    # Active offers — filter by the same as_of date used for prices
    as_of_str = str(as_of)
    offers = frappe.get_all(
        "CH Item Offer",
        filters=[
            ["item_code", "=", item_code],
            ["status", "in", ["Active", "Scheduled"]],
            ["approval_status", "=", "Approved"],
            ["start_date", "<=", as_of_str],
            ["end_date", ">=", as_of_str],
            ["channel", "in", [channel, ""]],
        ],
        fields=["offer_type", "value_type", "value", "priority", "stackable"],
    )

    best = _compute_best_offer(offers)
    selling = bp.selling_price or 0

    if best.get("price"):
        final = best["price"]
    elif best.get("label"):
        # Apply discounts if we can compute
        final = selling  # show base; frontend can compute
    else:
        final = selling

    return {
        "found": True,
        "base_price": dict(bp),
        "final_price": final,
        "offer_label": best.get("label", ""),
        "has_bank_offer": any(o.offer_type == "Bank Offer" for o in offers),
        "has_brand_offer": any(o.offer_type == "Brand Offer" for o in offers),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Approval actions
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def approve_price(price_name):
    """Approve a CH Item Price record.

    Delegates to CHItemPrice.approve() to ensure single code path
    for status computation, ERP sync, and permission checks.
    """
    frappe.only_for(["System Manager", "CH Master Manager"])
    doc = frappe.get_doc("CH Item Price", price_name)
    doc.approve()
    return "ok"


@frappe.whitelist()
def approve_offer(offer_name):
    """Approve a CH Item Offer."""
    frappe.only_for(["System Manager", "CH Master Manager"])
    doc = frappe.get_doc("CH Item Offer", offer_name)
    doc.approve()
    return "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Clone pricing from one item to another
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def clone_item_pricing(source_item, target_item, include_offers=True, effective_from=None):
    """Copy all price records (and optionally offers) from source → target."""
    frappe.only_for(["System Manager", "CH Master Manager"])

    eff_from = getdate(effective_from) if effective_from else getdate(nowdate())
    created = []

    prices = frappe.get_all(
        "CH Item Price",
        filters={"item_code": source_item, "status": ("in", ["Active", "Scheduled"])},
        fields=[
            "channel", "mrp", "mop", "selling_price", "notes",
        ],
    )
    for pr in prices:
        new_doc = frappe.new_doc("CH Item Price")
        new_doc.item_code     = target_item
        new_doc.channel       = pr.channel
        new_doc.mrp           = pr.mrp
        new_doc.mop           = pr.mop
        new_doc.selling_price = pr.selling_price
        new_doc.effective_from = str(eff_from)
        new_doc.status        = "Draft"
        new_doc.notes         = f"Cloned from {source_item}"
        new_doc.insert()
        created.append(new_doc.name)

    if include_offers:
        offers = frappe.get_all(
            "CH Item Offer",
            filters={"item_code": source_item, "status": ("in", ["Active", "Scheduled"])},
            fields=[
                "offer_name", "offer_type", "value_type", "value",
                "channel", "priority", "stackable", "start_date", "end_date",
                "bank_name", "card_type", "min_bill_amount", "payment_mode", "notes",
            ],
        )
        for of in offers:
            new_offer = frappe.new_doc("CH Item Offer")
            new_offer.item_code      = target_item
            new_offer.offer_name     = of.offer_name + " (Clone)"
            new_offer.offer_type     = of.offer_type
            new_offer.value_type     = of.value_type
            new_offer.value          = of.value
            new_offer.channel        = of.channel
            new_offer.priority       = of.priority
            new_offer.stackable      = of.stackable
            new_offer.start_date     = of.start_date
            new_offer.end_date       = of.end_date
            new_offer.bank_name      = of.bank_name
            new_offer.card_type      = of.card_type
            new_offer.min_bill_amount = of.min_bill_amount
            new_offer.payment_mode   = of.payment_mode
            new_offer.status         = "Draft"
            new_offer.notes          = f"Cloned from {source_item}"
            new_offer.insert()
            created.append(new_offer.name)

    # Note: Commit is handled by Frappe transaction boundaries
    return {"created": created, "count": len(created)}


# ─────────────────────────────────────────────────────────────────────────────
# Price propagation — apply price to sibling items sharing same price specs
# ─────────────────────────────────────────────────────────────────────────────

def _get_sibling_item_codes(item_code):
    """Find sibling items that share the same price-affecting variant attributes.

    For example, if item_code is a variant that differs from its siblings only
    in Colour (which has affects_price=0), return all of those sibling codes
    (including the item itself).

    Returns: list of item_codes, or [item_code] if no non-price specs exist.
    """
    # Get the item's template and sub-category
    item_info = frappe.db.get_value(
        "Item", item_code,
        ["variant_of", "ch_sub_category"],
        as_dict=True,
    )
    if not item_info or not item_info.variant_of:
        return [item_code]

    template = item_info.variant_of
    sub_cat = item_info.ch_sub_category or ""

    # Find non-price specs for this sub-category
    non_price_specs = set()
    spec_rows = frappe.get_all(
        "CH Sub Category Spec",
        filters={"parent": sub_cat, "is_variant": 1, "affects_price": 0},
        pluck="spec",
    )
    non_price_specs = set(spec_rows)

    if not non_price_specs:
        return [item_code]

    # Get all variants of the same template
    all_variants = frappe.get_all(
        "Item",
        filters={"variant_of": template, "disabled": 0},
        pluck="name",
    )
    if len(all_variants) <= 1:
        return [item_code]

    # Get variant attributes for source item
    source_attrs = {}
    for row in frappe.get_all(
        "Item Variant Attribute",
        filters={"parent": item_code},
        fields=["attribute", "attribute_value"],
    ):
        source_attrs[row.attribute] = row.attribute_value

    # Build the price-key for the source item (only price-affecting attrs)
    source_price_key = {
        k: v for k, v in source_attrs.items()
        if k not in non_price_specs
    }

    if not source_price_key:
        return [item_code]

    # Fetch all variant attributes in one query
    all_va = frappe.db.sql("""
        SELECT parent, attribute, attribute_value
        FROM `tabItem Variant Attribute`
        WHERE parent IN %(items)s
    """, {"items": all_variants}, as_dict=True)

    # Group by item
    item_attrs = {}
    for va in all_va:
        item_attrs.setdefault(va.parent, {})[va.attribute] = va.attribute_value

    # Find siblings with matching price-key
    siblings = []
    for variant in all_variants:
        attrs = item_attrs.get(variant, {})
        price_key = {k: v for k, v in attrs.items() if k not in non_price_specs}
        if price_key == source_price_key:
            siblings.append(variant)

    return siblings or [item_code]


@frappe.whitelist()
def get_sibling_items(item_code):
    """Return sibling item codes that share the same price-affecting specs.

    Used by the UI to show how many items will be affected by price propagation.
    """
    frappe.only_for(["System Manager", "CH Master Manager", "CH Price Manager"])
    siblings = _get_sibling_item_codes(item_code)
    return {
        "siblings": siblings,
        "count": len(siblings),
        "has_non_price_specs": len(siblings) > 1 or siblings != [item_code],
    }


@frappe.whitelist()
def save_price_with_propagation(
    item_code, channel, mrp=0, mop=0, selling_price=0,
    effective_from=None, notes="", company="",
    propagate=1, status="Active",
):
    """Create a CH Item Price for item_code and optionally propagate to sibling variants.

    When propagate=1, the same price is created/updated for all siblings that
    share the same price-affecting specs (e.g. same Network+Size+RAM+Storage but
    different Colour).
    """
    frappe.only_for(["System Manager", "CH Master Manager", "CH Price Manager"])

    propagate = int(propagate)
    mrp = float(mrp or 0)
    mop = float(mop or 0)
    selling_price = float(selling_price or 0)
    eff_from = effective_from or nowdate()

    if propagate:
        target_items = _get_sibling_item_codes(item_code)
    else:
        target_items = [item_code]

    created = []
    updated = []
    skipped = []

    for target in target_items:
        # Check for existing active price for same item+channel
        existing = frappe.db.get_value(
            "CH Item Price",
            {
                "item_code": target,
                "channel": channel,
                "status": ("in", ["Active", "Scheduled"]),
            },
            "name",
        )

        if existing:
            # Update existing price
            doc = frappe.get_doc("CH Item Price", existing)
            doc.mrp = mrp
            doc.mop = mop
            doc.selling_price = selling_price
            doc.effective_from = eff_from
            doc.notes = notes or doc.notes
            if company:
                doc.company = company
            doc.save(ignore_permissions=True)
            updated.append(doc.name)
        else:
            # Create new price
            doc = frappe.new_doc("CH Item Price")
            doc.item_code = target
            doc.channel = channel
            doc.mrp = mrp
            doc.mop = mop
            doc.selling_price = selling_price
            doc.effective_from = eff_from
            doc.status = status
            doc.notes = notes
            if company:
                doc.company = company
            doc.insert(ignore_permissions=True)
            created.append(doc.name)

    frappe.db.commit()

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_items": len(target_items),
        "target_items": target_items,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Excel export
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def export_ready_reckoner(
    category=None, sub_category=None, brand=None,
    model=None, channel=None, as_of_date=None, company=None,
):
    """Export CH Ready Reckoner as Excel file."""
    frappe.only_for(["System Manager", "CH Master Manager"])

    # Configurable max export rows (fallback to 5000 if not set)
    try:
        _MAX_EXPORT_ROWS = int(frappe.db.get_single_value("System Settings", "ch_max_export_rows") or 5000)
    except Exception:
        _MAX_EXPORT_ROWS = 5000
    data = get_ready_reckoner_data(
        category=category,
        sub_category=sub_category,
        brand=brand,
        model=model,
        channel=channel,
        as_of_date=as_of_date,
        company=company,
        page_length=_MAX_EXPORT_ROWS,
    )
    if data.get("total", 0) > _MAX_EXPORT_ROWS:
        frappe.msgprint(
            _("Export is limited to {0} rows. Use category / brand / model filters "
              "to narrow the selection and export in batches.").format(_MAX_EXPORT_ROWS),
            indicator="orange",
            title=_("Export Truncated"),
        )

    channels = data["channels"]
    rows_data = data["items"]

    # Build header row
    base_headers = [
        "Item Code", "Item Name", "Item Group", "Brand",
        "Category", "Sub Category", "Model",
    ]
    price_headers = []
    for ch in channels:
        price_headers += [f"{ch} MRP", f"{ch} MOP", f"{ch} Selling Price", f"{ch} Status"]

    extra_headers = [
        "Active Offer Count", "Active Offer Label",
        "Bank Offer", "Brand Offer", "Tags",
    ]

    headers = base_headers + price_headers + extra_headers

    xlsx_rows = [headers]
    for r in rows_data:
        row = [
            r.get("item_code"), r.get("item_name"), r.get("item_group"),
            r.get("brand"), r.get("ch_category"), r.get("ch_sub_category"), r.get("ch_model"),
        ]
        for ch in channels:
            row += [
                r.get(f"{ch}__mrp"),
                r.get(f"{ch}__mop"),
                r.get(f"{ch}__selling_price"),
                r.get(f"{ch}__status"),
            ]
        row += [
            r.get("offer_count"),
            r.get("active_offer_label"),
            "Yes" if r.get("has_bank_offer") else "No",
            "Yes" if r.get("has_brand_offer") else "No",
            r.get("tags"),
        ]
        xlsx_rows.append(row)

    xlsx_file = make_xlsx(xlsx_rows, "CH Ready Reckoner")
    frappe.response["filename"] = f"ch_ready_reckoner_{nowdate()}.xlsx"
    frappe.response["filecontent"] = xlsx_file.getvalue()
    frappe.response["type"] = "binary"
