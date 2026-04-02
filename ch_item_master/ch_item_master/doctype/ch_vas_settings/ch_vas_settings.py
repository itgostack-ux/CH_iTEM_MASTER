# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHVASSettings(Document):
	pass


def get_vas_settings():
	"""Return cached CH VAS Settings singleton.

	Usage:
		from ch_item_master.ch_item_master.doctype.ch_vas_settings.ch_vas_settings import get_vas_settings
		settings = get_vas_settings()
		threshold = settings.anniversary_threshold_months
	"""
	return frappe.get_cached_doc("CH VAS Settings")


def get_warranty_company():
	"""Shortcut: return the warranty issuer company name."""
	return get_vas_settings().gogizmo_company


def get_service_company():
	"""Shortcut: return the service provider company name."""
	return get_vas_settings().gofix_company


def get_fee_waiver_roles():
	"""Return list of roles allowed to approve fee waivers."""
	raw = get_vas_settings().fee_waiver_roles or ""
	return [r.strip() for r in raw.split("\n") if r.strip()]
