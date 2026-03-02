# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
Scheduled tasks for CH Item Master.

Registered in hooks.py under scheduler_events.
"""

import frappe
from frappe.utils import getdate, nowdate, now_datetime, get_datetime


def auto_expire_records():
	"""Daily job: expire CH Item Prices, Offers, and Commercial Tags whose end date has passed.

	This ensures Ready Reckoner, mobile API, and reports always show correct status
	without relying on someone manually opening and saving each record.

	Also activates Scheduled prices/offers whose start date has arrived.
	"""
	today = getdate(nowdate())
	now = now_datetime()

	# ── CH Item Price: Expire past-due, Activate scheduled ──
	expired_prices = frappe.db.sql("""
		UPDATE `tabCH Item Price`
		SET status = 'Expired', modified = NOW()
		WHERE status IN ('Active', 'Scheduled')
		  AND effective_to IS NOT NULL
		  AND effective_to < %s
	""", (str(today),))

	activated_prices = frappe.db.sql("""
		UPDATE `tabCH Item Price`
		SET status = 'Active', modified = NOW()
		WHERE status = 'Scheduled'
		  AND effective_from <= %s
		  AND (effective_to IS NULL OR effective_to >= %s)
	""", (str(today), str(today)))

	# Expire the linked ERPNext Item Prices for newly expired CH Item Prices
	newly_expired = frappe.get_all(
		"CH Item Price",
		filters={
			"status": "Expired",
			"erp_item_price": ("is", "set"),
			"modified": (">=", str(today)),
		},
		fields=["name", "erp_item_price"],
	)
	for rec in newly_expired:
		if frappe.db.exists("Item Price", rec.erp_item_price):
			valid_upto = frappe.db.get_value("Item Price", rec.erp_item_price, "valid_upto")
			if not valid_upto or getdate(valid_upto) >= today:
				frappe.db.set_value(
					"Item Price", rec.erp_item_price,
					"valid_upto", str(today),
					update_modified=False,
				)

	# ── CH Item Offer: Expire past-due, Activate scheduled ──
	frappe.db.sql("""
		UPDATE `tabCH Item Offer`
		SET status = 'Expired', modified = NOW()
		WHERE status IN ('Active', 'Scheduled')
		  AND approval_status = 'Approved'
		  AND end_date IS NOT NULL
		  AND end_date < %s
	""", (str(now),))

	frappe.db.sql("""
		UPDATE `tabCH Item Offer`
		SET status = 'Active', modified = NOW()
		WHERE status = 'Scheduled'
		  AND approval_status = 'Approved'
		  AND start_date <= %s
		  AND (end_date IS NULL OR end_date >= %s)
	""", (str(now), str(now)))

	# Disable Pricing Rules for newly expired offers
	newly_expired_offers = frappe.get_all(
		"CH Item Offer",
		filters={
			"status": "Expired",
			"erp_pricing_rule": ("is", "set"),
			"modified": (">=", str(today)),
		},
		pluck="erp_pricing_rule",
	)
	for pr_name in newly_expired_offers:
		if frappe.db.exists("Pricing Rule", pr_name):
			frappe.db.set_value("Pricing Rule", pr_name, "disable", 1, update_modified=False)

	# ── CH Item Commercial Tag: Expire past-due ──
	frappe.db.sql("""
		UPDATE `tabCH Item Commercial Tag`
		SET status = 'Expired', modified = NOW()
		WHERE status = 'Active'
		  AND effective_to IS NOT NULL
		  AND effective_to < %s
	""", (str(today),))

	frappe.db.commit()

	frappe.logger("ch_item_master").info(
		f"Auto-expire job completed. "
		f"Expired prices: {len(newly_expired)}, "
		f"Expired offers: {len(newly_expired_offers)}"
	)

	# ── CH Sold Plan: Expire past-due ──
	from ch_item_master.ch_item_master.warranty_api import expire_sold_plans
	expire_sold_plans()
