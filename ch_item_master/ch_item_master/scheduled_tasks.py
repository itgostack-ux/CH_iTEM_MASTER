# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Scheduled tasks for CH Item Master.

Registered in hooks.py under scheduler_events.
"""

import frappe
from frappe.utils import getdate, now_datetime, nowdate

from ch_item_master.config import get_int_setting


def _scheduler_batch_limit() -> int:
	return min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 5000)


def _bounded(rows, limit):
	return rows[:limit], len(rows) > limit


def auto_expire_records():
	"""Advance one bounded batch of time-sensitive commercial records."""
	today = getdate(nowdate())
	now = now_datetime()
	actor = frappe.session.user
	batch_limit = _scheduler_batch_limit()

	price_expiry_rows, more_price_expiries = _bounded(frappe.get_all(
		"CH Item Price",
		filters={
			"status": ("in", ("Active", "Scheduled")),
			"effective_to": ("<", str(today)),
		},
		fields=["name", "erp_item_price"],
		order_by="effective_to asc, name asc",
		limit=batch_limit + 1,
	), batch_limit)
	price_expiry_names = tuple(row.name for row in price_expiry_rows)
	if price_expiry_names:
		frappe.db.sql(
			"""
				UPDATE `tabCH Item Price`
				SET `status` = 'Expired', `modified` = %(modified)s, `modified_by` = %(actor)s
				WHERE `name` IN %(names)s
				  AND `status` IN ('Active', 'Scheduled')
				  AND `effective_to` < %(today)s
			""",
			{"names": price_expiry_names, "today": today, "modified": now, "actor": actor},
		)
		erp_price_names = tuple(
			dict.fromkeys(row.erp_item_price for row in price_expiry_rows if row.erp_item_price)
		)
		if erp_price_names:
			frappe.db.sql(
				"""
					UPDATE `tabItem Price`
					SET `valid_upto` = %(today)s
					WHERE `name` IN %(names)s
					  AND (`valid_upto` IS NULL OR `valid_upto` >= %(today)s)
				""",
				{"names": erp_price_names, "today": today},
			)

	price_activation_rows, more_price_activations = _bounded(frappe.get_all(
		"CH Item Price",
		filters={
			"status": "Scheduled",
			"effective_from": ("<=", str(today)),
		},
		or_filters=[
			["effective_to", "is", "not set"],
			["effective_to", ">=", str(today)],
		],
		pluck="name",
		order_by="effective_from asc, name asc",
		limit=batch_limit + 1,
	), batch_limit)
	price_activation_names = tuple(price_activation_rows)
	if price_activation_names:
		frappe.db.sql(
			"""
				UPDATE `tabCH Item Price`
				SET `status` = 'Active', `modified` = %(modified)s, `modified_by` = %(actor)s
				WHERE `name` IN %(names)s
				  AND `status` = 'Scheduled'
				  AND `effective_from` <= %(today)s
				  AND (`effective_to` IS NULL OR `effective_to` >= %(today)s)
			""",
			{
				"names": price_activation_names,
				"today": today,
				"modified": now,
				"actor": actor,
			},
		)

	offer_expiry_rows, more_offer_expiries = _bounded(frappe.get_all(
		"CH Item Offer",
		filters={
			"status": ("in", ("Active", "Scheduled")),
			"approval_status": "Approved",
			"end_date": ("<", now),
		},
		fields=["name", "erp_pricing_rule"],
		order_by="end_date asc, name asc",
		limit=batch_limit + 1,
	), batch_limit)
	offer_expiry_names = tuple(row.name for row in offer_expiry_rows)
	if offer_expiry_names:
		frappe.db.sql(
			"""
				UPDATE `tabCH Item Offer`
				SET `status` = 'Expired', `modified` = %(modified)s, `modified_by` = %(actor)s
				WHERE `name` IN %(names)s
				  AND `status` IN ('Active', 'Scheduled')
				  AND `approval_status` = 'Approved'
				  AND `end_date` < %(now)s
			""",
			{"names": offer_expiry_names, "now": now, "modified": now, "actor": actor},
		)
		pricing_rules = tuple(
			dict.fromkeys(row.erp_pricing_rule for row in offer_expiry_rows if row.erp_pricing_rule)
		)
		if pricing_rules:
			frappe.db.sql(
				"UPDATE `tabPricing Rule` SET `disable` = 1 WHERE `name` IN %(names)s",
				{"names": pricing_rules},
			)

	offer_activation_rows, more_offer_activations = _bounded(frappe.get_all(
		"CH Item Offer",
		filters={
			"status": "Scheduled",
			"approval_status": "Approved",
			"start_date": ("<=", now),
		},
		or_filters=[
			["end_date", "is", "not set"],
			["end_date", ">=", now],
		],
		pluck="name",
		order_by="start_date asc, name asc",
		limit=batch_limit + 1,
	), batch_limit)
	offer_activation_names = tuple(offer_activation_rows)
	if offer_activation_names:
		frappe.db.sql(
			"""
				UPDATE `tabCH Item Offer`
				SET `status` = 'Active', `modified` = %(modified)s, `modified_by` = %(actor)s
				WHERE `name` IN %(names)s
				  AND `status` = 'Scheduled'
				  AND `approval_status` = 'Approved'
				  AND `start_date` <= %(now)s
				  AND (`end_date` IS NULL OR `end_date` >= %(now)s)
			""",
			{"names": offer_activation_names, "now": now, "modified": now, "actor": actor},
		)

	tag_expiry_rows, more_tag_expiries = _bounded(frappe.get_all(
		"CH Item Commercial Tag",
		filters={"status": "Active", "effective_to": ("<", str(today))},
		pluck="name",
		order_by="effective_to asc, name asc",
		limit=batch_limit + 1,
	), batch_limit)
	tag_expiry_names = tuple(tag_expiry_rows)
	if tag_expiry_names:
		frappe.db.sql(
			"""
				UPDATE `tabCH Item Commercial Tag`
				SET `status` = 'Expired', `modified` = %(modified)s, `modified_by` = %(actor)s
				WHERE `name` IN %(names)s
				  AND `status` = 'Active'
				  AND `effective_to` < %(today)s
			""",
			{"names": tag_expiry_names, "today": today, "modified": now, "actor": actor},
		)

	from ch_item_master.ch_item_master.warranty_api import expire_sold_plans
	vas_result = expire_sold_plans(batch_limit=batch_limit)

	result = {
		"prices_expired": len(price_expiry_names),
		"prices_activated": len(price_activation_names),
		"offers_expired": len(offer_expiry_names),
		"offers_activated": len(offer_activation_names),
		"tags_expired": len(tag_expiry_names),
		"vas_plans_expired": (vas_result or {}).get("expired", 0),
		"has_more": any((
			more_price_expiries,
			more_price_activations,
			more_offer_expiries,
			more_offer_activations,
			more_tag_expiries,
			(vas_result or {}).get("has_more", False),
		)),
	}
	frappe.logger("ch_item_master").info(f"Commercial lifecycle scheduler: {result}")
	return result
