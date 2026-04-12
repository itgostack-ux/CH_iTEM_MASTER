# Copyright (c) 2026, GoStack and contributors
# Commercial Control API — price queries, discount validation, channel parity, override logging

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, now_datetime, cint


# ─────────────────────────────────────────────────────────────────────────────
# A3: Point-in-Time Price Query
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_price_as_of(item_code, channel, as_of_date=None, company=None) -> dict:
	"""Return the CH Item Price that was active for an item+channel on a given date.

	Used for: dispute resolution, returns validation, retroactive auditing.

	Args:
		item_code: Item Code
		channel: CH Price Channel name (e.g. "POS", "Website")
		as_of_date: Date string (YYYY-MM-DD). Defaults to today.
		company: Optional company filter.

	Returns:
		dict with price details or None if no price was active.
	"""
	if not item_code or not channel:
		frappe.throw(_("item_code and channel are required"), title=_("API Error"))

	target_date = getdate(as_of_date) if as_of_date else getdate(nowdate())

	filters = {
		"item_code": item_code,
		"channel": channel,
		"status": ("in", ["Active", "Scheduled", "Expired"]),
		"effective_from": ("<=", str(target_date)),
	}
	if company:
		filters["company"] = company

	# Find prices whose effective_from <= target_date AND (effective_to >= target_date OR no end)
	prices = frappe.get_all(
		"CH Item Price",
		filters=filters,
		or_filters=[
			["effective_to", "is", "not set"],
			["effective_to", ">=", str(target_date)],
		],
		fields=[
			"name", "item_code", "item_name", "channel", "company",
			"mrp", "mop", "selling_price",
			"effective_from", "effective_to", "status",
			"approved_by", "approved_at",
		],
		order_by="effective_from desc",
		limit=1,
	)

	if not prices:
		return None

	price = prices[0]
	price["as_of_date"] = str(target_date)
	return price


@frappe.whitelist()
def get_price_history(item_code, channel, company=None, limit=20) -> list:
	"""Return the full price history for an item+channel, newest first.

	Used for: audit trail, trend analysis.
	"""
	if not item_code or not channel:
		frappe.throw(_("item_code and channel are required"), title=_("API Error"))

	filters = {
		"item_code": item_code,
		"channel": channel,
	}
	if company:
		filters["company"] = company

	return frappe.get_all(
		"CH Item Price",
		filters=filters,
		fields=[
			"name", "mrp", "mop", "selling_price",
			"effective_from", "effective_to", "status",
			"approved_by", "approved_at",
		],
		order_by="effective_from desc",
		limit_page_length=cint(limit) or 20,
	)


# ─────────────────────────────────────────────────────────────────────────────
# A1: POS Discount Validation
# ─────────────────────────────────────────────────────────────────────────────

def get_commercial_policy(company):
	"""Fetch the CH Commercial Policy for a company. Returns dict or None."""
	try:
		policy = frappe.get_cached_doc("CH Commercial Policy")
	except frappe.DoesNotExistError:
		return None
	if not policy.enabled or policy.company != company:
		return None
	return policy


def validate_pos_discount(item_code, channel, rate, company, user=None):
	"""Validate that a POS item rate doesn't violate commercial policy.

	Args:
		item_code: Item Code
		channel: Price channel name (usually "POS")
		rate: The rate being charged
		company: Company
		user: POS user (defaults to session user)

	Returns:
		dict with validation result:
		  - allowed: bool
		  - needs_approval: bool
		  - reason: str
		  - original_price: float
		  - discount_percent: float
		  - max_allowed_percent: float
	"""
	user = user or frappe.session.user
	rate = flt(rate)

	# Get the active CH Item Price for this item+channel
	ch_price = frappe.db.get_value(
		"CH Item Price",
		{"item_code": item_code, "channel": channel, "status": "Active", "company": company},
		["selling_price", "mop", "mrp", "name"],
		as_dict=True,
	)

	if not ch_price:
		# No CH price — allow (ERPNext standard pricing applies)
		return {"allowed": True, "needs_approval": False, "reason": "No CH price record"}

	selling_price = flt(ch_price.selling_price)
	mop = flt(ch_price.mop)

	if rate >= selling_price:
		# No discount at all
		return {"allowed": True, "needs_approval": False, "reason": "No discount applied"}

	discount_amount = selling_price - rate
	discount_percent = (discount_amount / selling_price * 100) if selling_price else 0

	# Check MOP floor
	if mop and rate < mop:
		# Check if item has allowed tags
		policy = get_commercial_policy(company)
		allowed_tags = []
		if policy and policy.allow_below_mop_for_tags:
			import re
			allowed_tags = [t.strip().upper() for t in re.split(r"[,\n]+", policy.allow_below_mop_for_tags) if t.strip()]

		if allowed_tags:
			active_tags = frappe.get_all(
				"CH Item Commercial Tag",
				filters={"item_code": item_code, "status": "Active"},
				pluck="tag",
			)
			has_allowed_tag = any(t.upper() in allowed_tags for t in active_tags)
			if has_allowed_tag:
				return {
					"allowed": True,
					"needs_approval": True,
					"reason": f"Below MOP (allowed for tagged item)",
					"original_price": selling_price,
					"discount_percent": discount_percent,
				}

		return {
			"allowed": False,
			"needs_approval": False,
			"reason": f"Rate {rate} is below MOP {mop}. Not allowed.",
			"original_price": selling_price,
			"mop": mop,
			"discount_percent": discount_percent,
		}

	# Get role-based limits
	policy = get_commercial_policy(company)
	max_allowed = 100  # default: no limit

	if policy:
		# Check global max first
		global_max = flt(policy.max_discount_without_approval)
		if global_max > 0:
			max_allowed = global_max

		# Check role-specific limits (most permissive role wins)
		user_roles = set(frappe.get_roles(user))
		for limit_row in policy.discount_limits:
			if limit_row.role in user_roles:
				role_max = flt(limit_row.max_discount_percent)
				if role_max > max_allowed:
					max_allowed = role_max

	if discount_percent > max_allowed:
		return {
			"allowed": False,
			"needs_approval": True,
			"reason": f"Discount {discount_percent:.1f}% exceeds max allowed {max_allowed:.1f}%",
			"original_price": selling_price,
			"discount_percent": discount_percent,
			"max_allowed_percent": max_allowed,
		}

	return {
		"allowed": True,
		"needs_approval": False,
		"reason": "Within limits",
		"original_price": selling_price,
		"discount_percent": discount_percent,
		"max_allowed_percent": max_allowed,
	}


# ─────────────────────────────────────────────────────────────────────────────
# POS Override Logging
# ─────────────────────────────────────────────────────────────────────────────

def log_pos_override(pos_invoice, item_code, original_price, applied_price,
                     override_type="Rate Override", serial_no=None,
                     approved_by_manager=False, manager_user=None,
                     override_reason=None, pos_profile=None, company=None,
                     warehouse=None):
	"""Create a CH POS Override Log entry.

	Called from POS Invoice validate hook when rate differs from CH Item Price.
	"""
	policy = get_commercial_policy(company) if company else None
	if policy and not policy.log_all_pos_overrides:
		return

	original_price = flt(original_price)
	applied_price = flt(applied_price)
	if original_price <= 0:
		return

	discount_amount = original_price - applied_price
	discount_percent = (discount_amount / original_price * 100) if original_price else 0

	log = frappe.new_doc("CH POS Override Log")
	log.pos_invoice = pos_invoice
	log.pos_profile = pos_profile
	log.company = company or ""
	log.store_warehouse = warehouse or ""
	log.item_code = item_code
	log.serial_no = serial_no or ""
	log.override_type = override_type
	log.original_price = original_price
	log.applied_price = applied_price
	log.discount_percent = discount_percent
	log.discount_amount = discount_amount
	log.approved_by_manager = 1 if approved_by_manager else 0
	log.manager_user = manager_user or ""
	log.override_reason = override_reason or ""
	log.pos_user = frappe.session.user
	log.override_at = now_datetime()
	log.insert(ignore_permissions=True)
	return log.name


# ─────────────────────────────────────────────────────────────────────────────
# A2: Offer Precedence — used by POS validate hook
# ─────────────────────────────────────────────────────────────────────────────

def check_offer_precedence(item_code, channel, company):
	"""Check if a CH Item Offer is active for this item+channel.

	If yes, the POS Invoice validate hook should set ignore_pricing_rule=1
	on the item to prevent ERPNext Pricing Rule from double-applying.

	Returns:
		dict or None — the active CH Item Offer details if found.
	"""
	now = now_datetime()
	offer = frappe.db.get_value(
		"CH Item Offer",
		{
			"item_code": item_code,
			"channel": channel,
			"status": "Active",
			"company": company,
			"start_date": ("<=", now),
			"end_date": (">=", now),
		},
		["name", "offer_name", "offer_type", "value_type", "value", "erp_pricing_rule"],
		as_dict=True,
	)
	return offer


# ─────────────────────────────────────────────────────────────────────────────
# B3: Channel Parity Check (called via scheduled task)
# ─────────────────────────────────────────────────────────────────────────────

def run_channel_parity_check():
	"""Check all active items for channel price divergence.

	Creates ToDo alerts for items where prices differ across channels
	by more than the configured threshold.
	"""
	try:
		policy = frappe.get_cached_doc("CH Commercial Policy")
	except frappe.DoesNotExistError:
		return
	if not policy or not policy.enabled or not policy.enable_channel_parity_check:
		return

	threshold = flt(policy.channel_parity_threshold) or 5
	alert_role = policy.parity_alert_role or "CH Price Manager"
	company = policy.company

	# Get all items with active prices in multiple channels
	items_with_prices = frappe.db.sql("""
		SELECT item_code, channel, selling_price
		FROM `tabCH Item Price`
		WHERE status = 'Active' AND company = %s
		ORDER BY item_code, channel
	""", (company,), as_dict=True)

	# Group by item
	from collections import defaultdict
	item_channels = defaultdict(dict)
	for row in items_with_prices:
		item_channels[row.item_code][row.channel] = flt(row.selling_price)

	divergent_items = []
	for item_code, channels in item_channels.items():
		if len(channels) < 2:
			continue

		prices = list(channels.values())
		min_price = min(prices)
		max_price = max(prices)
		if min_price <= 0:
			continue

		spread_pct = ((max_price - min_price) / min_price) * 100
		if spread_pct > threshold:
			divergent_items.append({
				"item_code": item_code,
				"channels": channels,
				"spread_pct": round(spread_pct, 1),
			})

	if not divergent_items:
		return

	# Get users with the alert role
	alert_users = frappe.get_all(
		"Has Role",
		filters={"role": alert_role, "parenttype": "User"},
		pluck="parent",
	)

	# Create a single ToDo alert
	description_lines = [
		f"**{d['item_code']}**: {d['spread_pct']}% spread — "
		+ ", ".join(f"{ch}: ₹{p:,.0f}" for ch, p in d["channels"].items())
		for d in divergent_items[:20]  # Limit to top 20
	]
	if len(divergent_items) > 20:
		description_lines.append(f"... and {len(divergent_items) - 20} more items")

	description = (
		f"**Channel Price Divergence Alert**\n\n"
		f"{len(divergent_items)} item(s) have price spread > {threshold}% across channels:\n\n"
		+ "\n".join(description_lines)
	)

	for user in alert_users[:3]:  # Alert top 3 users
		existing = frappe.db.exists("ToDo", {
			"reference_type": "CH Commercial Policy",
			"reference_name": "CH Commercial Policy",
			"allocated_to": user,
			"status": "Open",
		})
		if existing:
			continue

		frappe.get_doc({
			"doctype": "ToDo",
			"allocated_to": user,
			"reference_type": "CH Commercial Policy",
			"reference_name": "CH Commercial Policy",
			"description": description,
			"priority": "Medium",
			"date": nowdate(),
		}).insert(ignore_permissions=True)


# ─────────────────────────────────────────────────────────────────────────────
# E1: Tag-Based Auto-Repricing Scheduler
# ─────────────────────────────────────────────────────────────────────────────

def run_tag_auto_repricing():
	"""Scheduled task: auto-tag items based on stock movement and apply repricing.

	Logic:
	1. Items with no sales in slow_moving_days → tag SLOW MOVING
	2. Items with no sales in dead_stock_days → tag DEAD STOCK
	3. Items tagged EOL → auto-markdown selling_price by eol_auto_markdown_percent
	4. Items tagged CLEARANCE → auto-markdown by clearance percentage (uses EOL %)
	5. Items tagged DEAD STOCK → auto-markdown by dead_stock_markdown_percent

	Only runs if CH Commercial Policy.enable_tag_auto_pricing is ON.
	"""
	try:
		policy = frappe.get_cached_doc("CH Commercial Policy")
	except frappe.DoesNotExistError:
		return
	if not policy or not policy.enabled or not policy.enable_tag_auto_pricing:
		return

	company = policy.company
	slow_days = policy.slow_moving_days or 90
	dead_days = policy.dead_stock_days or 180
	eol_markdown = flt(policy.eol_auto_markdown_percent) or 20

	today = getdate(nowdate())
	from datetime import timedelta

	# ── Step 1: Auto-tag items based on sales velocity ────────────────
	_auto_tag_slow_and_dead_stock(company, slow_days, dead_days, today)

	# ── Step 2: Auto-markdown prices for tagged items ─────────────────
	tagged_items = frappe.get_all(
		"CH Item Commercial Tag",
		filters={
			"status": "Active",
			"tag": ("in", ["EOL", "CLEARANCE", "DEAD STOCK"]),
		},
		fields=["item_code", "tag", "name"],
	)

	if not tagged_items:
		return

	markdown_count = 0
	for tag_rec in tagged_items:
		# Determine markdown percentage based on tag
		if tag_rec.tag in ("EOL", "CLEARANCE"):
			markdown_pct = eol_markdown
		elif tag_rec.tag == "DEAD STOCK":
			# Dead stock gets a larger markdown (1.5x EOL rate)
			markdown_pct = min(eol_markdown * 1.5, 50)
		else:
			continue

		# Apply markdown to all active CH Item Prices for this item
		active_prices = frappe.get_all(
			"CH Item Price",
			filters={
				"item_code": tag_rec.item_code,
				"company": company,
				"status": "Active",
			},
			fields=["name", "selling_price", "mop", "mrp"],
		)

		for price in active_prices:
			new_price = round(flt(price.selling_price) * (1 - markdown_pct / 100), 2)
			# Don't markdown below MOP unless it's clearance/dead stock
			mop_floor = flt(price.mop)
			if tag_rec.tag not in ("CLEARANCE", "DEAD STOCK") and mop_floor and new_price < mop_floor:
				new_price = mop_floor

			if new_price >= flt(price.selling_price):
				continue  # Already at or below target

			# Check if already marked down (prevent re-applying)
			already_marked = frappe.db.exists("CH Price Change Log", {
				"item_code": tag_rec.item_code,
				"source": "Tag Auto-Reprice",
				"changed_at": (">=", str(today)),
			})
			if already_marked:
				continue

			# Update via direct SQL to bypass price governance (this IS the governance)
			frappe.db.set_value(
				"CH Item Price", price.name,
				"selling_price", new_price,
				update_modified=True,
			)

			# Trigger sync to ERPNext Item Price
			ch_price_doc = frappe.get_doc("CH Item Price", price.name)
			ch_price_doc._sync_to_erp_item_price()

			# Log the change
			frappe.get_doc({
				"doctype": "CH Price Change Log",
				"item_code": tag_rec.item_code,
				"item_name": frappe.db.get_value("Item", tag_rec.item_code, "item_name"),
				"channel": ch_price_doc.channel,
				"change_type": "Update",
				"field_name": "selling_price",
				"field_label": "Selling Price",
				"old_value": str(price.selling_price),
				"new_value": str(new_price),
				"source": "Tag Auto-Reprice",
				"reason": f"Auto-markdown {markdown_pct}% for {tag_rec.tag} tag",
				"changed_by": "Administrator",
				"changed_at": now_datetime(),
			}).insert(ignore_permissions=True)

			markdown_count += 1

	if markdown_count:
		frappe.db.commit()
		frappe.logger("ch_item_master").info(
			f"Tag auto-repricing: {markdown_count} price(s) marked down"
		)


def _auto_tag_slow_and_dead_stock(company, slow_days, dead_days, today):
	"""Auto-tag items as SLOW MOVING or DEAD STOCK based on sales velocity.

	Uses Sales Invoice Item (submitted) to determine last sale date per item.
	"""
	from datetime import timedelta

	slow_cutoff = str(today - timedelta(days=slow_days))
	dead_cutoff = str(today - timedelta(days=dead_days))

	# Get last sale date for all items in this company
	last_sales = frappe.db.sql("""
		SELECT sii.item_code, MAX(si.posting_date) as last_sale
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1 AND si.company = %s
		GROUP BY sii.item_code
	""", (company,), as_dict=True)

	last_sale_map = {r.item_code: r.last_sale for r in last_sales}

	# Get all stocked items for this company
	stock_items = frappe.get_all(
		"Item",
		filters={"is_stock_item": 1, "disabled": 0},
		pluck="name",
	)

	for item_code in stock_items:
		last_sale = last_sale_map.get(item_code)

		# Skip items never sold (might be new inventory)
		if not last_sale:
			continue

		last_sale_date = getdate(last_sale)

		# Check existing tags
		existing_tags = frappe.get_all(
			"CH Item Commercial Tag",
			filters={"item_code": item_code, "status": "Active"},
			pluck="tag",
		)

		# Dead stock check (more severe — takes priority)
		if last_sale_date <= getdate(dead_cutoff):
			if "DEAD STOCK" not in existing_tags:
				_create_auto_tag(item_code, "DEAD STOCK", company,
					f"No sales in {dead_days}+ days (last: {last_sale_date})")
				# Remove SLOW MOVING if upgrading to DEAD STOCK
				_expire_tag(item_code, "SLOW MOVING")
		# Slow moving check
		elif last_sale_date <= getdate(slow_cutoff):
			if "SLOW MOVING" not in existing_tags and "DEAD STOCK" not in existing_tags:
				_create_auto_tag(item_code, "SLOW MOVING", company,
					f"No sales in {slow_days}+ days (last: {last_sale_date})")


def _create_auto_tag(item_code, tag, company, reason):
	"""Create a CH Item Commercial Tag automatically."""
	frappe.get_doc({
		"doctype": "CH Item Commercial Tag",
		"item_code": item_code,
		"company": company,
		"tag": tag,
		"status": "Active",
		"effective_from": nowdate(),
		"reason": reason,
	}).insert(ignore_permissions=True)


def _expire_tag(item_code, tag):
	"""Expire an existing active tag."""
	existing = frappe.db.get_value(
		"CH Item Commercial Tag",
		{"item_code": item_code, "tag": tag, "status": "Active"},
		"name",
	)
	if existing:
		frappe.db.set_value("CH Item Commercial Tag", existing, {
			"status": "Expired",
			"effective_to": nowdate(),
		})
