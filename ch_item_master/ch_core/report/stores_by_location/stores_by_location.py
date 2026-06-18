# Copyright (c) 2026, GoGizmo
# License: MIT
"""Stores by Location

A pivot of CH Store records joined with CH City and CH State so users can
quickly answer questions like "which cities is BestBuy in?" or "how many
GoFix stores are in Karnataka?". The report intentionally stays a thin
read view — all the relationships already exist on CH Store; we just
surface them in one screen.
"""

from __future__ import annotations

import frappe
from frappe import _


def execute(filters: dict | None = None):
	filters = frappe._dict(filters or {})
	columns = _get_columns()
	data = _get_data(filters)
	chart = _get_chart(data, filters)
	summary = _get_summary(data)
	return columns, data, None, chart, summary


def _get_columns() -> list[dict]:
	return [
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 180,
		},
		{
			"label": _("State Code"),
			"fieldname": "state_code",
			"fieldtype": "Data",
			"width": 90,
		},
		{
			"label": _("State"),
			"fieldname": "state",
			"fieldtype": "Link",
			"options": "CH State",
			"width": 140,
		},
		{
			"label": _("City"),
			"fieldname": "city",
			"fieldtype": "Link",
			"options": "CH City",
			"width": 160,
		},
		{
			"label": _("Zone"),
			"fieldname": "zone",
			"fieldtype": "Link",
			"options": "CH Store Zone",
			"width": 160,
		},
		{
			"label": _("Store Code"),
			"fieldname": "store_code",
			"fieldtype": "Link",
			"options": "CH Store",
			"width": 150,
		},
		{
			"label": _("Store Name"),
			"fieldname": "store_name",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Pincode"),
			"fieldname": "pincode",
			"fieldtype": "Data",
			"width": 90,
		},
		{
			"label": _("Buyback"),
			"fieldname": "is_buyback_enabled",
			"fieldtype": "Check",
			"width": 80,
		},
		{
			"label": _("Service"),
			"fieldname": "is_service_enabled",
			"fieldtype": "Check",
			"width": 80,
		},
		{
			"label": _("Retail"),
			"fieldname": "is_retail_enabled",
			"fieldtype": "Check",
			"width": 80,
		},
		{
			"label": _("Default Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 200,
		},
		{
			"label": _("Status"),
			"fieldname": "status",
			"fieldtype": "Data",
			"width": 90,
		},
	]


def _get_data(filters: frappe._dict) -> list[dict]:
	conditions = ["1=1"]
	values: dict = {}

	if filters.get("company"):
		conditions.append("s.company = %(company)s")
		values["company"] = filters.company

	if filters.get("state"):
		conditions.append("(s.state = %(state)s OR c.state = %(state)s)")
		values["state"] = filters.state

	if filters.get("city"):
		conditions.append("s.city = %(city)s")
		values["city"] = filters.city

	if filters.get("zone"):
		conditions.append("s.zone = %(zone)s")
		values["zone"] = filters.zone

	if filters.get("capability") == "Buyback":
		conditions.append("s.is_buyback_enabled = 1")
	elif filters.get("capability") == "Service":
		conditions.append("s.is_service_enabled = 1")
	elif filters.get("capability") == "Retail":
		conditions.append("s.is_retail_enabled = 1")

	status = filters.get("status") or "Active"
	if status == "Active":
		conditions.append("IFNULL(s.disabled, 0) = 0")
	elif status == "Disabled":
		conditions.append("IFNULL(s.disabled, 0) = 1")
	# else "All" — no filter

	where_clause = " AND ".join(conditions)

	rows = frappe.db.sql(
		f"""
		SELECT
			s.company,
			COALESCE(st.state_code, '') AS state_code,
			COALESCE(s.state, c.state) AS state,
			s.city,
			s.zone,
			s.name AS store_code,
			s.store_name,
			s.pincode,
			IFNULL(s.is_buyback_enabled, 0) AS is_buyback_enabled,
			IFNULL(s.is_service_enabled, 0) AS is_service_enabled,
			IFNULL(s.is_retail_enabled, 0) AS is_retail_enabled,
			s.warehouse,
			CASE WHEN IFNULL(s.disabled, 0) = 1 THEN 'Disabled' ELSE 'Active' END AS status
		FROM `tabCH Store` s
		LEFT JOIN `tabCH City` c ON c.name = s.city
		LEFT JOIN `tabCH State` st ON st.name = COALESCE(s.state, c.state)
		WHERE {where_clause}
		ORDER BY s.company, state_code, s.city, s.zone, s.store_name
		""",
		values,
		as_dict=True,
	)
	return rows


def _get_chart(data: list[dict], filters: frappe._dict) -> dict | None:
	if not data:
		return None
	# Stores per company × state — gives a quick "footprint" picture.
	bucket: dict[tuple[str, str], int] = {}
	companies: list[str] = []
	states: list[str] = []
	for row in data:
		company = row.get("company") or _("Unassigned")
		state = row.get("state_code") or row.get("state") or _("Unassigned")
		if company not in companies:
			companies.append(company)
		if state not in states:
			states.append(state)
		bucket[(company, state)] = bucket.get((company, state), 0) + 1

	datasets = [
		{
			"name": company,
			"values": [bucket.get((company, state), 0) for state in states],
		}
		for company in companies
	]
	return {
		"data": {
			"labels": states,
			"datasets": datasets,
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"title": _("Stores by Company and State"),
	}


def _get_summary(data: list[dict]) -> list[dict]:
	total_stores = len(data)
	companies = len({r.get("company") for r in data if r.get("company")})
	cities = len({r.get("city") for r in data if r.get("city")})
	states = len({r.get("state_code") or r.get("state") for r in data if r.get("state_code") or r.get("state")})
	zones = len({r.get("zone") for r in data if r.get("zone")})
	return [
		{"label": _("Total Stores"), "value": total_stores, "datatype": "Int"},
		{"label": _("Companies"), "value": companies, "datatype": "Int"},
		{"label": _("States"), "value": states, "datatype": "Int"},
		{"label": _("Cities"), "value": cities, "datatype": "Int"},
		{"label": _("Zones"), "value": zones, "datatype": "Int"},
	]
