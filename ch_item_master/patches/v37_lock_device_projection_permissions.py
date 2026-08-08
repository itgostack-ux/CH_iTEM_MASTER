"""Remove site-level permission overrides that bypass projection APIs."""

import frappe


def execute():
	for table in ("tabDocPerm", "tabCustom DocPerm"):
		frappe.db.sql(
			f"""
			UPDATE `{table}`
			   SET `write` = 0, `create` = 0, `delete` = 0, `import` = 0
			 WHERE parent IN ('CH Customer Device', 'CH Serial Lifecycle')
			"""
		)
	frappe.clear_cache(doctype="CH Customer Device")
	frappe.clear_cache(doctype="CH Serial Lifecycle")
