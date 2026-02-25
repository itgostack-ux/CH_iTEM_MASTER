# Copyright (c) 2026, GoStack and contributors
# Mobile-optimized API endpoints using integer IDs

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


@frappe.whitelist(allow_guest=False)
def get_categories_mobile():
	"""Return categories with compact integer IDs for mobile app"""
	return frappe.db.sql("""
		SELECT 
			category_id as id,
			category_name as name,
			item_group,
			is_active
		FROM `tabCH Category`
		WHERE is_active = 1
		ORDER BY category_name
	""", as_dict=True)


@frappe.whitelist(allow_guest=False)
def get_sub_categories_mobile(category_id=None):
	"""Return sub-categories for a category using integer IDs"""
	conditions = ["is_active = 1"]
	values = {}
	
	if category_id:
		# Convert integer ID to string name for query
		category_name = frappe.db.get_value(
			"CH Category", 
			{"category_id": category_id}, 
			"name"
		)
		if category_name:
			conditions.append("category = %(category)s")
			values["category"] = category_name
	
	query = """
		SELECT 
			sub_category_id as id,
			sub_category_name as name,
			category,
			hsn_code,
			gst_rate,
			prefix
		FROM `tabCH Sub Category`
		WHERE {conditions}
		ORDER BY sub_category_name
	""".format(conditions=" AND ".join(conditions))
	
	return frappe.db.sql(query, values, as_dict=True)


@frappe.whitelist(allow_guest=False)
def get_models_mobile(sub_category_id=None, limit=50, offset=0):
	"""Return models with integer IDs and pagination"""
	conditions = ["is_active = 1"]
	values = {"limit": int(limit), "offset": int(offset)}
	
	if sub_category_id:
		sc_name = frappe.db.get_value(
			"CH Sub Category",
			{"sub_category_id": sub_category_id},
			"name"
		)
		if sc_name:
			conditions.append("sub_category = %(sub_category)s")
			values["sub_category"] = sc_name
	
	query = """
		SELECT 
			model_id as id,
			model_name as name,
			sub_category,
			manufacturer,
			brand
		FROM `tabCH Model`
		WHERE {conditions}
		ORDER BY model_name
		LIMIT %(limit)s OFFSET %(offset)s
	""".format(conditions=" AND ".join(conditions))
	
	return frappe.db.sql(query, values, as_dict=True)


@frappe.whitelist(allow_guest=False)
def get_model_details_mobile(model_id):
	"""Get model details using integer ID"""
	# Convert integer ID to string name
	model_name = frappe.db.get_value(
		"CH Model",
		{"model_id": model_id},
		"name"
	)
	
	if not model_name:
		frappe.throw(_("Model with ID {0} not found").format(model_id))
	
	# Reuse existing API logic
	from ch_item_master.ch_item_master.api import get_model_details
	result = get_model_details(model_name)
	
	# Add integer IDs to response
	if result:
		result["model_id"] = int(model_id)
		# Add sub_category_id
		if result.get("sub_category"):
			result["sub_category_id"] = frappe.db.get_value(
				"CH Sub Category",
				result["sub_category"],
				"sub_category_id"
			)
		# Add category_id
		if result.get("category"):
			result["category_id"] = frappe.db.get_value(
				"CH Category",
				result["category"],
				"category_id"
			)
	
	return result


@frappe.whitelist(allow_guest=False)
def get_channels_mobile():
	"""Return all active price channels with integer IDs"""
	return frappe.db.sql("""
		SELECT 
			channel_id as id,
			channel_name as name,
			description,
			is_active,
			is_buying
		FROM `tabCH Price Channel`
		WHERE is_active = 1
		ORDER BY channel_name
	""", as_dict=True)


@frappe.whitelist(allow_guest=False)
def get_active_price_mobile(item_code, channel_id, as_of_date=None):
	"""Get price using integer channel ID"""
	# Convert channel_id to channel name
	channel_name = frappe.db.get_value(
		"CH Price Channel",
		{"channel_id": channel_id},
		"name"
	)
	
	if not channel_name:
		frappe.throw(_("Channel with ID {0} not found").format(channel_id))
	
	# Reuse existing API logic
	from ch_item_master.ch_item_master.ready_reckoner_api import get_active_price
	result = get_active_price(item_code, channel_name, as_of_date)
	
	# Add integer channel_id to response
	if result and result.get("found"):
		result["channel_id"] = int(channel_id)
	
	return result


@frappe.whitelist(allow_guest=False)
def get_ready_reckoner_mobile(
	sub_category_id=None,
	model_id=None,
	channel_id=None,
	limit=50,
	offset=0
):
	"""Mobile-optimized ready reckoner with compact IDs"""
	
	# Convert IDs to names
	filters = {}
	
	if sub_category_id:
		filters["sub_category"] = frappe.db.get_value(
			"CH Sub Category",
			{"sub_category_id": sub_category_id},
			"name"
		)
	
	if model_id:
		filters["model"] = frappe.db.get_value(
			"CH Model",
			{"model_id": model_id},
			"name"
		)
	
	if channel_id:
		filters["channel"] = frappe.db.get_value(
			"CH Price Channel",
			{"channel_id": channel_id},
			"name"
		)
	
	# Use existing ready reckoner logic
	from ch_item_master.ch_item_master.ready_reckoner_api import get_ready_reckoner_data
	
	result = get_ready_reckoner_data(
		sub_category=filters.get("sub_category"),
		model=filters.get("model"),
		channel=filters.get("channel"),
		limit=int(limit),
		offset=int(offset)
	)
	
	# Add integer IDs to response for easier mobile handling
	if result and result.get("items"):
		for item in result["items"]:
			# Add model_id
			if item.get("model"):
				item["model_id"] = frappe.db.get_value(
					"CH Model",
					item["model"],
					"model_id"
				)
			
			# Add sub_category_id
			if item.get("sub_category"):
				item["sub_category_id"] = frappe.db.get_value(
					"CH Sub Category",
					item["sub_category"],
					"sub_category_id"
				)
			
			# Add channel IDs to price data
			if item.get("prices"):
				for price_key in list(item["prices"].keys()):
					channel_name = price_key
					channel_id_val = frappe.db.get_value(
						"CH Price Channel",
						channel_name,
						"channel_id"
					)
					if channel_id_val:
						item["prices"][price_key]["channel_id"] = channel_id_val
	
	return result


@frappe.whitelist(allow_guest=False)
def search_items_mobile(query, channel_id=None, limit=20):
	"""Search items by name/code with optional channel price - optimized for mobile"""
	conditions = ["i.disabled = 0"]
	values = {"query": f"%{query}%", "limit": int(limit)}
	
	if channel_id:
		channel_name = frappe.db.get_value(
			"CH Price Channel",
			{"channel_id": channel_id},
			"name"
		)
		if channel_name:
			values["channel"] = channel_name
	
	# Search query
	sql = """
		SELECT 
			i.item_code,
			i.item_name,
			i.ch_model as model,
			i.ch_sub_category as sub_category,
			i.image
		FROM `tabItem` i
		WHERE (i.item_code LIKE %(query)s OR i.item_name LIKE %(query)s)
			AND {conditions}
		ORDER BY 
			CASE 
				WHEN i.item_code LIKE %(query)s THEN 1
				ELSE 2
			END,
			i.item_name
		LIMIT %(limit)s
	""".format(conditions=" AND ".join(conditions))
	
	items = frappe.db.sql(sql, values, as_dict=True)
	
	# Add integer IDs and prices
	for item in items:
		if item.get("model"):
			item["model_id"] = frappe.db.get_value(
				"CH Model",
				item["model"],
				"model_id"
			)
		
		if item.get("sub_category"):
			item["sub_category_id"] = frappe.db.get_value(
				"CH Sub Category",
				item["sub_category"],
				"sub_category_id"
			)
		
		# Get price for channel if specified
		if channel_id and "channel" in values:
			price_data = frappe.db.get_value(
				"CH Item Price",
				{
					"item_code": item["item_code"],
					"channel": values["channel"],
					"status": ["in", ["Active", "Scheduled"]]
				},
				["selling_price", "mrp", "mop"],
				as_dict=True
			)
			if price_data:
				item["price"] = price_data
				item["price"]["channel_id"] = int(channel_id)
	
	return items
