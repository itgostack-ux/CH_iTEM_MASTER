# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

def boot_session(bootinfo):
	"""Push ch_item_master settings to client at login."""
	import frappe
	if frappe.db.exists("DocType", "CH Item Master Settings"):
		settings = frappe.get_cached_doc("CH Item Master Settings")
		bootinfo["ch_item_master_settings"] = {
			"default_warranty_days": getattr(settings, "default_warranty_days", 365),
			"enable_lifecycle_tracking": getattr(settings, "enable_lifecycle_tracking", 1),
		}
