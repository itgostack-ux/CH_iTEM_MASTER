# Copyright (c) 2026, GoStack and contributors
# Commercial Control API — price queries, discount validation, channel parity, override logging

import hashlib

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

from ch_item_master.config import (
	get_enabled_role_emails,
	get_enabled_role_users,
	get_int_setting,
	get_list_setting,
	get_role_setting,
	get_user_roles,
	require_role_setting,
)
from ch_item_master.security import ensure_company_access


def _validate_price_query(item_code, channel, company):
	require_role_setting(
		"price_view_roles",
		("CH Viewer", "CH Price Manager", "CH Master Manager"),
		action=_("view commercial prices"),
	)
	if not item_code or not channel or not company:
		frappe.throw(
			_("Item Code, Price Channel, and Company are required."),
			frappe.ValidationError,
		)
	frappe.has_permission("Item", "read", item_code, throw=True)
	frappe.has_permission("CH Price Channel", "read", channel, throw=True)
	frappe.has_permission("CH Item Price", "read", throw=True)
	frappe.has_permission("Company", "read", company, throw=True)
	ensure_company_access(company)


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
	_validate_price_query(item_code, channel, company)

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
	_validate_price_query(item_code, channel, company)
	row_limit = max(
		1,
		min(
			cint(limit) or 20,
			get_int_setting("price_history_limit", 100, minimum=1),
		),
	)

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
		limit_page_length=row_limit,
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
		user_roles = get_user_roles(user)
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
                     warehouse=None, customer=None, posting_date=None):
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

	currency = None
	if company:
		currency = frappe.db.get_value("Company", company, "default_currency")

	log = frappe.new_doc("CH POS Override Log")
	log.pos_invoice = pos_invoice
	log.pos_profile = pos_profile
	log.company = company or ""
	log.store_warehouse = warehouse or ""
	log.currency = currency or ""
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
	log.customer = customer or ""
	log.posting_date = posting_date or nowdate()
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
	"""Evaluate a bounded aggregate of divergent channel prices."""
	try:
		policy = frappe.get_cached_doc("CH Commercial Policy")
	except frappe.DoesNotExistError:
		return {"divergent": 0, "alerts_created": 0, "has_more": False}
	if not policy or not policy.enabled or not policy.enable_channel_parity_check:
		return {"divergent": 0, "alerts_created": 0, "has_more": False}

	threshold = flt(policy.channel_parity_threshold) or 5
	company = policy.company
	if not company:
		return {"divergent": 0, "alerts_created": 0, "has_more": False}

	batch_limit = min(
		get_int_setting("commercial_scheduler_batch_limit", 500, minimum=1),
		5000,
	)
	rows = frappe.db.sql(
		"""
			SELECT `item_code`,
			       ROUND(
				       ((MAX(`selling_price`) - MIN(`selling_price`))
				        / NULLIF(MIN(`selling_price`), 0)) * 100,
				       1
			       ) AS `spread_pct`
			FROM `tabCH Item Price`
			WHERE `status` = 'Active' AND `company` = %(company)s
			GROUP BY `item_code`
			HAVING COUNT(DISTINCT `channel`) >= 2
			   AND MIN(`selling_price`) > 0
			   AND ((MAX(`selling_price`) - MIN(`selling_price`))
			        / MIN(`selling_price`)) * 100 > %(threshold)s
			ORDER BY `spread_pct` DESC, `item_code` ASC
			LIMIT %(fetch_limit)s
		""",
		{
			"company": company,
			"threshold": threshold,
			"fetch_limit": batch_limit + 1,
		},
		as_dict=True,
	)
	divergent_items = rows[:batch_limit]
	if not divergent_items:
		return {"divergent": 0, "alerts_created": 0, "has_more": False}

	alert_item_limit = min(
		get_int_setting("commercial_alert_item_limit", 20, minimum=1),
		batch_limit,
	)
	displayed = divergent_items[:alert_item_limit]
	item_codes = tuple(row.item_code for row in displayed)
	channel_rows = frappe.db.sql(
		"""
			SELECT `item_code`, `channel`, `selling_price`
			FROM `tabCH Item Price`
			WHERE `status` = 'Active'
			  AND `company` = %(company)s
			  AND `item_code` IN %(item_codes)s
			ORDER BY `item_code` ASC, `channel` ASC, `name` ASC
			LIMIT %(row_limit)s
		""",
		{
			"company": company,
			"item_codes": item_codes,
			"row_limit": batch_limit,
		},
		as_dict=True,
	)
	channels_by_item = {item_code: {} for item_code in item_codes}
	for row in channel_rows:
		channels_by_item.setdefault(row.item_code, {})[row.channel] = flt(row.selling_price)

	alert_roles = (
		(policy.parity_alert_role,)
		if policy.parity_alert_role
		else get_role_setting("price_approval_roles", ())
	)
	alert_users = get_enabled_role_users(
		alert_roles,
		company=company,
	)

	description_lines = [
		f"**{row.item_code}**: {flt(row.spread_pct):.1f}% spread — "
		+ ", ".join(
			f"{channel}: ₹{price:,.0f}"
			for channel, price in channels_by_item.get(row.item_code, {}).items()
		)
		for row in displayed
	]
	if len(divergent_items) > alert_item_limit or len(rows) > batch_limit:
		description_lines.append("... additional divergent items remain in the bounded review queue")

	description = (
		f"**Channel Price Divergence Alert**\n\n"
		f"{len(divergent_items)} item(s) have price spread > {threshold}% across channels:\n\n"
		+ "\n".join(description_lines)
	)

	existing_users = set()
	if alert_users:
		existing_users = set(frappe.get_all(
			"ToDo",
			filters={
			"reference_type": "CH Commercial Policy",
			"reference_name": "CH Commercial Policy",
			"allocated_to": ("in", tuple(alert_users)),
			"status": "Open",
			},
			pluck="allocated_to",
			limit=len(alert_users),
		))

	alerts_created = 0
	for user in alert_users:
		if user in existing_users:
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
		alerts_created += 1
	return {
		"divergent": len(divergent_items),
		"alerts_created": alerts_created,
		"has_more": len(rows) > batch_limit,
	}


# ─────────────────────────────────────────────────────────────────────────────
# E1: Tag-Based Auto-Repricing Scheduler
# ─────────────────────────────────────────────────────────────────────────────

def run_tag_auto_repricing():
	"""Tag slow stock and apply each active tag to each price exactly once."""
	try:
		policy = frappe.get_cached_doc("CH Commercial Policy")
	except frappe.DoesNotExistError:
		return {"tagged": 0, "prices_updated": 0, "has_more": False}
	if not policy or not policy.enabled or not policy.enable_tag_auto_pricing:
		return {"tagged": 0, "prices_updated": 0, "has_more": False}

	company = policy.company
	if not company:
		return {"tagged": 0, "prices_updated": 0, "has_more": False}
	slow_days = policy.slow_moving_days or 90
	dead_days = policy.dead_stock_days or 180
	eol_markdown = min(max(flt(policy.eol_auto_markdown_percent) or 20, 0), 99)
	dead_markdown = min(max(flt(policy.get("dead_stock_markdown_percent")) or 30, 0), 99)
	batch_limit = min(
		get_int_setting("commercial_scheduler_batch_limit", 500, minimum=1),
		5000,
	)

	tag_result = _auto_tag_slow_and_dead_stock(
		company,
		slow_days,
		dead_days,
		getdate(nowdate()),
		batch_limit,
	)
	price_rows = frappe.db.sql(
		"""
			SELECT tag.`name` AS `tag_name`, tag.`tag`, tag.`item_code`,
			       item.`item_name`, price.`name` AS `price_name`, price.`channel`,
			       price.`selling_price`, price.`mop`, price.`erp_item_price`,
			       linked_price.`name` AS `linked_erp_item_price`
			FROM `tabCH Item Commercial Tag` tag
			INNER JOIN `tabCH Item Price` price
			  ON price.`item_code` = tag.`item_code`
			 AND price.`company` = tag.`company`
			 AND price.`status` = 'Active'
			INNER JOIN `tabItem` item ON item.`name` = tag.`item_code`
			LEFT JOIN `tabItem Price` linked_price ON linked_price.`name` = price.`erp_item_price`
			LEFT JOIN `tabCH Price Change Log` change_log
			  ON change_log.`source` = 'Tag Auto-Reprice'
			 AND change_log.`automation_reference` = SHA2(
			       CONCAT(tag.`name`, CHAR(31), price.`name`), 256
			 )
			WHERE tag.`company` = %(company)s
			  AND tag.`status` = 'Active'
			  AND tag.`tag` IN ('EOL', 'CLEARANCE', 'DEAD STOCK')
			  AND change_log.`name` IS NULL
			ORDER BY tag.`name` ASC, price.`name` ASC
			LIMIT %(fetch_limit)s
		""",
		{"company": company, "fetch_limit": batch_limit + 1},
		as_dict=True,
	)
	work_rows = price_rows[:batch_limit]
	changes = []
	for row in work_rows:
		markdown_pct = dead_markdown if row.tag == "DEAD STOCK" else eol_markdown
		old_price = flt(row.selling_price)
		new_price = round(old_price * (1 - markdown_pct / 100), 2)
		mop_floor = flt(row.mop)
		if row.tag == "EOL" and mop_floor and new_price < mop_floor:
			new_price = mop_floor
		new_price = max(new_price, 0.01)
		row["markdown_pct"] = markdown_pct
		row["new_price"] = min(new_price, old_price)
		row["automation_reference"] = hashlib.sha256(
			f"{row.tag_name}\x1f{row.price_name}".encode()
		).hexdigest()
		if row.new_price < old_price:
			changes.append(row)

	if changes:
		case_fragments = []
		params = []
		for row in changes:
			case_fragments.append("WHEN %s THEN %s")
			params.extend((row.price_name, row.new_price))
		name_placeholders = ", ".join(["%s"] * len(changes))
		now = now_datetime()
		params.extend((now, frappe.session.user))
		params.extend(row.price_name for row in changes)
		frappe.db.sql(
			f"""
				UPDATE `tabCH Item Price`
				SET `selling_price` = CASE `name` {' '.join(case_fragments)} ELSE `selling_price` END,
				    `modified` = %s,
				    `modified_by` = %s
				WHERE `name` IN ({name_placeholders}) AND `status` = 'Active'
			""",
			tuple(params),
		)
		frappe.db.sql(
			f"""
				UPDATE `tabItem Price` linked_price
				INNER JOIN `tabCH Item Price` source_price
				  ON source_price.`erp_item_price` = linked_price.`name`
				SET linked_price.`price_list_rate` = source_price.`selling_price`,
				    linked_price.`modified` = %s,
				    linked_price.`modified_by` = %s
				WHERE source_price.`name` IN ({name_placeholders})
			""",
			(now, frappe.session.user, *(row.price_name for row in changes)),
		)
		for row in changes:
			if not row.linked_erp_item_price:
				frappe.get_doc("CH Item Price", row.price_name)._sync_to_erp_item_price()

	if work_rows:
		now = now_datetime()
		actor = frappe.session.user
		frappe.db.bulk_insert(
			"CH Price Change Log",
			fields=[
				"name", "creation", "modified", "owner", "modified_by", "docstatus", "idx",
				"item_code", "item_name", "channel", "change_type", "field_name", "field_label",
				"old_value", "new_value", "source", "automation_reference", "reason",
				"changed_by", "changed_at",
			],
			values=[(
				frappe.generate_hash(length=10),
				now,
				now,
				actor,
				actor,
				0,
				0,
				row.item_code,
				row.item_name,
				row.channel,
				"Selling Price",
				"selling_price",
				"Selling Price",
				str(row.selling_price),
				str(row.new_price),
				"Tag Auto-Reprice",
				row.automation_reference,
				(
					f"Auto-markdown {row.markdown_pct}% for {row.tag} tag"
					if row.new_price < flt(row.selling_price)
					else f"No markdown required for {row.tag} tag at the configured price floor"
				),
				actor,
				now,
			) for row in work_rows],
		)

	result = {
		"tagged": tag_result["tagged"],
		"tag_failures": tag_result["failed"],
		"prices_evaluated": len(work_rows),
		"prices_updated": len(changes),
		"has_more": tag_result["has_more"] or len(price_rows) > batch_limit,
	}
	frappe.logger("ch_item_master").info(f"Tag auto-repricing scheduler: {result}")
	return result


def _auto_tag_slow_and_dead_stock(company, slow_days, dead_days, today, batch_limit):
	"""Create one bounded batch of missing velocity tags."""
	from datetime import timedelta

	slow_cutoff = today - timedelta(days=cint(slow_days) or 90)
	dead_cutoff = today - timedelta(days=cint(dead_days) or 180)
	rows = frappe.db.sql(
		"""
			SELECT velocity.`item_code`, velocity.`last_sale`,
			       CASE
				       WHEN velocity.`last_sale` <= %(dead_cutoff)s THEN 'DEAD STOCK'
				       ELSE 'SLOW MOVING'
			       END AS `target_tag`
			FROM (
				SELECT item.`name` AS `item_code`, MAX(invoice.`posting_date`) AS `last_sale`
				FROM `tabItem` item
				INNER JOIN `tabSales Invoice Item` invoice_item
				  ON invoice_item.`item_code` = item.`name`
				INNER JOIN `tabSales Invoice` invoice
				  ON invoice.`name` = invoice_item.`parent`
				 AND invoice.`docstatus` = 1
				 AND invoice.`company` = %(company)s
				WHERE item.`is_stock_item` = 1 AND item.`disabled` = 0
				GROUP BY item.`name`
			) velocity
			WHERE (
				velocity.`last_sale` <= %(dead_cutoff)s
				AND NOT EXISTS (
					SELECT 1 FROM `tabCH Item Commercial Tag` existing_dead
					WHERE existing_dead.`company` = %(company)s
					  AND existing_dead.`item_code` = velocity.`item_code`
					  AND existing_dead.`status` = 'Active'
					  AND existing_dead.`tag` = 'DEAD STOCK'
				)
			) OR (
				velocity.`last_sale` > %(dead_cutoff)s
				AND velocity.`last_sale` <= %(slow_cutoff)s
				AND NOT EXISTS (
					SELECT 1 FROM `tabCH Item Commercial Tag` existing_slow
					WHERE existing_slow.`company` = %(company)s
					  AND existing_slow.`item_code` = velocity.`item_code`
					  AND existing_slow.`status` = 'Active'
					  AND existing_slow.`tag` IN ('SLOW MOVING', 'DEAD STOCK')
				)
			)
			ORDER BY velocity.`last_sale` ASC, velocity.`item_code` ASC
			LIMIT %(fetch_limit)s
		""",
		{
			"company": company,
			"slow_cutoff": slow_cutoff,
			"dead_cutoff": dead_cutoff,
			"fetch_limit": batch_limit + 1,
		},
		as_dict=True,
	)
	candidates = rows[:batch_limit]
	created = 0
	failed = 0
	dead_items = []
	for index, row in enumerate(candidates):
		save_point = f"commercial_auto_tag_{index}"
		frappe.db.savepoint(save_point)
		try:
			frappe.get_doc({
				"doctype": "CH Item Commercial Tag",
				"item_code": row.item_code,
				"company": company,
				"tag": row.target_tag,
				"status": "Active",
				"effective_from": nowdate(),
				"reason": (
					f"No sales in {dead_days}+ days (last: {getdate(row.last_sale)})"
					if row.target_tag == "DEAD STOCK"
					else f"No sales in {slow_days}+ days (last: {getdate(row.last_sale)})"
				),
			}).insert(ignore_permissions=True)
			created += 1
			if row.target_tag == "DEAD STOCK":
				dead_items.append(row.item_code)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			failed += 1
			frappe.log_error(
				frappe.get_traceback(),
				f"Commercial auto-tag failed for {row.item_code}",
			)
	if dead_items:
		frappe.db.sql(
			"""
				UPDATE `tabCH Item Commercial Tag`
				SET `status` = 'Expired', `effective_to` = %(today)s
				WHERE `company` = %(company)s
				  AND `item_code` IN %(item_codes)s
				  AND `tag` = 'SLOW MOVING'
				  AND `status` = 'Active'
			""",
			{"company": company, "item_codes": tuple(dead_items), "today": today},
		)
	return {
		"tagged": created,
		"failed": failed,
		"has_more": len(rows) > batch_limit or bool(failed),
	}


# ─────────────────────────────────────────────────────────────────────────────
# POS Override threshold monitor
# ─────────────────────────────────────────────────────────────────────────────
# Mirrors Oracle Retail Xstore "Excessive Override Threshold Alert" and
# NCR Counterpoint "Manager Override Threshold". Runs hourly via scheduler.
# Sends one consolidated digest email per company when one or more cashiers
# exceed the configured per-day threshold.

def monitor_pos_override_thresholds():
	"""Scheduled hourly job — flag cashiers exceeding the per-day override threshold."""
	today = nowdate()

	# CH Commercial Policy is a Single doctype today (one row per site). We read
	# the single doc and apply its threshold to the company it points at. If/when
	# this is converted to a per-company table, this loop trivially generalises.
	try:
		policy = frappe.get_cached_doc("CH Commercial Policy")
	except frappe.DoesNotExistError:
		return

	if not cint(policy.get("enabled")) or not cint(policy.get("log_all_pos_overrides")):
		return

	threshold = cint(policy.get("override_alert_threshold"))
	if threshold <= 0:
		return

	company = policy.get("company")
	if not company:
		return
	batch_limit = min(
		get_int_setting("commercial_scheduler_batch_limit", 500, minimum=1),
		5000,
	)

	offenders = frappe.db.sql(
		"""
		SELECT pos_user,
		       COUNT(*) AS override_count,
		       SUM(IF(IFNULL(approved_by_manager,0)=0,1,0)) AS unapproved_count,
		       SUM(discount_amount) AS leakage
		FROM `tabCH POS Override Log`
		WHERE company = %(company)s
		  AND posting_date = %(today)s
		GROUP BY pos_user
		HAVING override_count > %(threshold)s
		ORDER BY override_count DESC, pos_user ASC
		LIMIT %(batch_limit)s
		""",
		{
			"company": company,
			"today": today,
			"threshold": threshold,
			"batch_limit": batch_limit,
		},
		as_dict=True,
	)

	if not offenders:
		return

	_send_override_threshold_alert(
		company=company,
		threshold=threshold,
		offenders=offenders,
		alert_roles=get_list_setting(
			"pos_override_notification_roles",
			(
				(policy.get("override_alert_role"),)
				if policy.get("override_alert_role")
				else get_role_setting("price_approval_roles", ())
			),
		),
	)


def _send_override_threshold_alert(company, threshold, offenders, alert_roles):
	"""Send a digest email to enabled users in the configured business roles."""
	recipients = get_enabled_role_emails(alert_roles, company=company)
	if not recipients:
		return
	currency = frappe.db.get_value("Company", company, "default_currency")

	rows_html = "".join(
		f"<tr>"
		f"<td>{frappe.utils.escape_html(o.pos_user or '—')}</td>"
		f"<td style='text-align:right'>{cint(o.override_count)}</td>"
		f"<td style='text-align:right'>{cint(o.unapproved_count)}</td>"
		f"<td style='text-align:right'>{frappe.utils.fmt_money(flt(o.leakage), currency=currency)}</td>"
		f"</tr>"
		for o in offenders
	)

	body = f"""
		<p>The following cashiers exceeded the daily POS override threshold
		   (<b>{threshold}</b>) for <b>{frappe.utils.escape_html(company)}</b> on
		   <b>{nowdate()}</b>:</p>
		<table border="1" cellpadding="6" cellspacing="0"
		       style="border-collapse:collapse;font-family:sans-serif;font-size:13px;">
			<thead style="background:#f5f5f5;">
				<tr>
					<th align="left">Cashier</th>
					<th align="right">Overrides</th>
					<th align="right">Unapproved</th>
					<th align="right">Margin Leakage</th>
				</tr>
			</thead>
			<tbody>{rows_html}</tbody>
		</table>
		<p style="margin-top:16px;">
			Review the
			<a href="/app/query-report/CH POS Override Audit?company={frappe.utils.quoted(company)}&from_date={nowdate()}&to_date={nowdate()}">
			CH POS Override Audit report</a> for full details.
		</p>
	"""

	try:
		frappe.sendmail(
			recipients=list(recipients),
			subject=f"[POS Override Alert] {company} — {len(offenders)} cashier(s) over threshold",
			message=body,
			reference_doctype="CH Commercial Policy",
			reference_name=company,
			delayed=False,
		)
	except Exception:
		frappe.log_error(
			title="POS Override threshold alert failed",
			message=frappe.get_traceback(),
		)
