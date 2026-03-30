# Copyright (c) 2025, GoStack and contributors
# CEO Alert Engine — runs every 30 minutes via scheduler

import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime, add_to_date, flt, cint, getdate


def check_ceo_alerts():
	"""Scheduled: evaluate all alert rules and create/expire CH CEO Alert records."""
	_expire_old_alerts()

	settings = _get_settings()
	stores = frappe.get_all("POS Profile", filters={"disabled": 0}, pluck="name")

	today = getdate(nowdate())
	for store in stores:
		_check_low_conversion(store, today, settings)
		_check_low_gocare_attach(store, today, settings)
		_check_high_discount(store, today, settings)


def _get_settings():
	"""Return CH CEO Dashboard Settings or defaults."""
	try:
		return frappe.get_cached_doc("CH CEO Dashboard Settings")
	except Exception:
		return frappe._dict(
			low_conversion_threshold=40,
			low_gocare_attach_threshold=25,
			high_discount_threshold=8,
			alert_expiry_hours=24,
		)


def _expire_old_alerts():
	"""Deactivate alerts past their expiry time."""
	frappe.db.sql("""
		UPDATE `tabCH CEO Alert`
		SET is_active = 0
		WHERE is_active = 1
			AND expires_at IS NOT NULL
			AND expires_at < NOW()
	""")


def _alert_exists(alert_type, store):
	"""Check if an active alert already exists for this type+store."""
	return frappe.db.exists("CH CEO Alert", {
		"alert_type": alert_type,
		"store": store,
		"is_active": 1,
	})


def _create_alert(alert_type, severity, store, message, expiry_hours=24):
	if _alert_exists(alert_type, store):
		return
	frappe.get_doc({
		"doctype": "CH CEO Alert",
		"alert_type": alert_type,
		"severity": severity,
		"store": store,
		"message": message,
		"is_active": 1,
		"expires_at": add_to_date(now_datetime(), hours=expiry_hours),
	}).insert(ignore_permissions=True)


def _check_low_conversion(store, today, settings):
	threshold = cint(settings.low_conversion_threshold) or 40

	tokens = frappe.db.count("CH Kiosk Token", filters={
		"pos_profile": store,
		"creation": [">=", today],
	})
	if tokens < 5:
		return  # Not enough data

	invoices = frappe.db.count("Sales Invoice", filters={
		"pos_profile": store,
		"posting_date": today,
		"docstatus": 1,
	})

	conversion = flt(invoices / tokens * 100, 1)
	if conversion < threshold:
		_create_alert(
			"Low Conversion", "Warning", store,
			f"Conversion at {conversion}% ({invoices}/{tokens} tokens). Threshold: {threshold}%",
			cint(settings.alert_expiry_hours) or 24
		)


def _check_low_gocare_attach(store, today, settings):
	threshold = cint(settings.low_gocare_attach_threshold) or 25

	if not frappe.db.table_exists("tabCH Attach Log"):
		return

	data = frappe.db.sql("""
		SELECT COUNT(*) as total,
			SUM(CASE WHEN action = 'Accepted' THEN 1 ELSE 0 END) as accepted
		FROM `tabCH Attach Log`
		WHERE DATE(offered_at) = %s
			AND pos_profile IN (SELECT name FROM `tabPOS Profile` WHERE name = %s)
			AND attach_type = 'Warranty'
	""", (today, store), as_dict=1)

	if not data or not data[0].total or data[0].total < 3:
		return

	rate = flt(data[0].accepted / data[0].total * 100, 1)
	if rate < threshold:
		_create_alert(
			"Low GoCare Attach", "Warning", store,
			f"GoCare attach rate at {rate}% ({data[0].accepted}/{data[0].total}). Threshold: {threshold}%",
			cint(settings.alert_expiry_hours) or 24
		)


def _check_high_discount(store, today, settings):
	threshold = cint(settings.high_discount_threshold) or 8

	data = frappe.db.sql("""
		SELECT
			COUNT(*) as total_items,
			SUM(CASE WHEN sii.discount_percentage > 0 THEN 1 ELSE 0 END) as disc_items
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1
			AND si.posting_date = %s
			AND si.pos_profile = %s
	""", (today, store), as_dict=1)

	if not data or not data[0].total_items or data[0].total_items < 5:
		return

	rate = flt(data[0].disc_items / data[0].total_items * 100, 1)
	if rate > threshold:
		_create_alert(
			"High Discount Rate", "Warning", store,
			f"Discount override rate at {rate}% ({data[0].disc_items}/{data[0].total_items} items). Threshold: {threshold}%",
			cint(settings.alert_expiry_hours) or 24
		)
