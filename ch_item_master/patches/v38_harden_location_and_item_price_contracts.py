"""Reconcile store geography and publish every approved CH price to ERPNext."""

import frappe


def execute():
	if frappe.db.table_exists("CH Store") and frappe.db.table_exists("Warehouse"):
		from ch_item_master.ch_core.location_hierarchy import repair_retail_location_integrity

		repair_retail_location_integrity()

	if not (frappe.db.table_exists("CH Item Price") and frappe.db.table_exists("Item Price")):
		return
	missing_channel = frappe.db.sql(
		"""
		SELECT p.name
		  FROM `tabCH Item Price` p
		  LEFT JOIN `tabCH Price Channel` c ON c.name = p.channel
		  LEFT JOIN `tabPrice List` pl ON pl.name = c.price_list
		 WHERE p.status IN ('Active', 'Scheduled')
		   AND (IFNULL(c.price_list, '') = '' OR IFNULL(pl.enabled, 0) = 0)
		 LIMIT 1
		"""
	)
	if missing_channel:
		frappe.throw(
			f"Approved CH Item Price {missing_channel[0][0]} has no enabled ERPNext Price List."
		)

	ambiguous = frappe.db.sql(
		"""
		SELECT a.name, b.name
		  FROM `tabCH Item Price` a
		  JOIN `tabCH Item Price` b
		    ON b.item_code = a.item_code
		   AND b.channel = a.channel
		   AND b.company != a.company
		   AND b.name > a.name
		   AND b.status IN ('Active', 'Scheduled')
		   AND (a.effective_to IS NULL OR b.effective_from <= a.effective_to)
		   AND (b.effective_to IS NULL OR a.effective_from <= b.effective_to)
		 WHERE a.status IN ('Active', 'Scheduled')
		 LIMIT 1
		"""
	)
	if ambiguous:
		frappe.throw(
			"Company-scoped CH prices {0} and {1} collide in one ERPNext Price List. "
			"Assign company-specific Channels/Price Lists.".format(*ambiguous[0])
		)

	prices = frappe.db.sql_list(
		"""
		SELECT p.name
		  FROM `tabCH Item Price` p
		  JOIN `tabCH Price Channel` c ON c.name = p.channel
		  LEFT JOIN `tabItem Price` ip ON ip.name = p.erp_item_price
		 WHERE p.status IN ('Active', 'Scheduled')
		   AND (
			   ip.name IS NULL
			OR ip.ch_source_price != p.name
			OR ip.item_code != p.item_code
			OR ip.price_list != c.price_list
			OR ip.price_list_rate != p.selling_price
			OR IFNULL(ip.ch_mop, 0) != IFNULL(p.mop, 0)
			OR IFNULL(ip.valid_from, '1000-01-01') != IFNULL(p.effective_from, '1000-01-01')
			OR IFNULL(ip.valid_upto, '9999-12-31') != IFNULL(p.effective_to, '9999-12-31')
		   )
		 ORDER BY p.name
		"""
	)
	for name in prices:
		doc = frappe.get_doc("CH Item Price", name)
		doc._validate_erp_projection_contract()
		doc._sync_to_erp_item_price()
