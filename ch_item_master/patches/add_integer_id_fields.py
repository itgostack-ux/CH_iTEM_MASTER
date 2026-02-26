# Copyright (c) 2026, GoStack and contributors
# Migration patch to add integer ID indexes and backfill existing records

import frappe
from frappe.utils import cint


def execute():
	"""Add indexes on integer ID fields and backfill existing records with IDs"""
	
	frappe.db.commit()
	
	# Backfill CH Category IDs
	backfill_category_ids()
	
	# Backfill CH Sub Category IDs
	backfill_sub_category_ids()
	
	# Backfill CH Model IDs
	backfill_model_ids()
	
	# Backfill CH Price Channel IDs
	backfill_channel_ids()
	
	# Backfill CH Warranty Plan IDs
	backfill_warranty_plan_ids()
	
	# Backfill Manufacturer IDs (Custom Field)
	backfill_manufacturer_ids()
	
	# Backfill Brand IDs (Custom Field)
	backfill_brand_ids()
	
	# Add unique indexes
	add_indexes()
	
	frappe.db.commit()
	print("✅ Integer ID migration completed successfully")


def backfill_category_ids():
	"""Backfill category_id for existing CH Category records"""
	categories = frappe.get_all(
		"CH Category", 
		fields=["name", "category_id"],
		order_by="creation asc"
	)
	
	if not categories:
		return
	
	print(f"Backfilling {len(categories)} CH Category records...")
	
	for idx, cat in enumerate(categories, start=1):
		if not cat.category_id:
			frappe.db.set_value(
				"CH Category", 
				cat.name, 
				"category_id", 
				idx, 
				update_modified=False
			)
	
	print(f"✅ Backfilled {len(categories)} CH Category IDs")


def backfill_sub_category_ids():
	"""Backfill sub_category_id for existing CH Sub Category records"""
	subcats = frappe.get_all(
		"CH Sub Category", 
		fields=["name", "sub_category_id"],
		order_by="creation asc"
	)
	
	if not subcats:
		return
	
	print(f"Backfilling {len(subcats)} CH Sub Category records...")
	
	for idx, sc in enumerate(subcats, start=1):
		if not sc.sub_category_id:
			frappe.db.set_value(
				"CH Sub Category", 
				sc.name, 
				"sub_category_id", 
				idx, 
				update_modified=False
			)
	
	print(f"✅ Backfilled {len(subcats)} CH Sub Category IDs")


def backfill_model_ids():
	"""Backfill model_id for existing CH Model records"""
	models = frappe.get_all(
		"CH Model", 
		fields=["name", "model_id"],
		order_by="creation asc"
	)
	
	if not models:
		return
	
	print(f"Backfilling {len(models)} CH Model records...")
	
	for idx, model in enumerate(models, start=1):
		if not model.model_id:
			frappe.db.set_value(
				"CH Model", 
				model.name, 
				"model_id", 
				idx, 
				update_modified=False
			)
	
	print(f"✅ Backfilled {len(models)} CH Model IDs")


def backfill_channel_ids():
	"""Backfill channel_id for existing CH Price Channel records"""
	channels = frappe.get_all(
		"CH Price Channel", 
		fields=["name", "channel_id"],
		order_by="creation asc"
	)
	
	if not channels:
		return
	
	print(f"Backfilling {len(channels)} CH Price Channel records...")
	
	for idx, ch in enumerate(channels, start=1):
		if not ch.channel_id:
			frappe.db.set_value(
				"CH Price Channel", 
				ch.name, 
				"channel_id", 
				idx, 
				update_modified=False
			)
	
	print(f"✅ Backfilled {len(channels)} CH Price Channel IDs")


def backfill_warranty_plan_ids():
	"""Backfill warranty_plan_id for existing CH Warranty Plan records"""
	plans = frappe.get_all(
		"CH Warranty Plan", 
		fields=["name", "warranty_plan_id"],
		order_by="creation asc"
	)
	
	if not plans:
		return
	
	print(f"Backfilling {len(plans)} CH Warranty Plan records...")
	
	for idx, plan in enumerate(plans, start=1):
		if not plan.warranty_plan_id:
			frappe.db.set_value(
				"CH Warranty Plan", 
				plan.name, 
				"warranty_plan_id", 
				idx, 
				update_modified=False
			)
	
	print(f"✅ Backfilled {len(plans)} CH Warranty Plan IDs")


def backfill_manufacturer_ids():
	"""Backfill manufacturer_id for existing Manufacturer records"""
	# First ensure custom field exists
	if not frappe.db.exists("Custom Field", "Manufacturer-manufacturer_id"):
		print("⚠️  Custom field 'Manufacturer-manufacturer_id' not found. Creating...")
		try:
			doc = frappe.get_doc({
				"doctype": "Custom Field",
				"dt": "Manufacturer",
				"fieldname": "manufacturer_id",
				"label": "Manufacturer ID",
				"fieldtype": "Int",
				"insert_after": "name",
				"read_only": 1,
				"unique": 1,
				"no_copy": 1,
				"description": "Auto-generated sequential ID for mobile/API integration"
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print("✅ Created custom field for Manufacturer")
		except Exception as e:
			print(f"⚠️ Could not create Manufacturer custom field: {e}")
			return
	
	manufacturers = frappe.get_all(
		"Manufacturer", 
		fields=["name", "manufacturer_id"],
		order_by="creation asc"
	)
	
	if not manufacturers:
		return
	
	print(f"Backfilling {len(manufacturers)} Manufacturer records...")
	
	for idx, mfr in enumerate(manufacturers, start=1):
		if not mfr.manufacturer_id:
			frappe.db.set_value(
				"Manufacturer", 
				mfr.name, 
				"manufacturer_id", 
				idx, 
				update_modified=False
			)
	
	print(f"✅ Backfilled {len(manufacturers)} Manufacturer IDs")


def backfill_brand_ids():
	"""Backfill brand_id for existing Brand records"""
	# First ensure custom field exists
	if not frappe.db.exists("Custom Field", "Brand-brand_id"):
		print("⚠️ Custom field 'Brand-brand_id' not found. Creating...")
		try:
			doc = frappe.get_doc({
				"doctype": "Custom Field",
				"dt": "Brand",
				"fieldname": "brand_id",
				"label": "Brand ID",
				"fieldtype": "Int",
				"insert_after": "name",
				"read_only": 1,
				"unique": 1,
				"no_copy": 1,
				"description": "Auto-generated sequential ID for mobile/API integration"
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print("✅ Created custom field for Brand")
		except Exception as e:
			print(f"⚠️ Could not create Brand custom field: {e}")
			return
	
	brands = frappe.get_all(
		"Brand", 
		fields=["name", "brand_id"],
		order_by="creation asc"
	)
	
	if not brands:
		return
	
	print(f"Backfilling {len(brands)} Brand records...")
	
	for idx, brand in enumerate(brands, start=1):
		if not brand.brand_id:
			frappe.db.set_value(
				"Brand", 
				brand.name, 
				"brand_id", 
				idx, 
				update_modified=False
			)
	
	print(f"✅ Backfilled {len(brands)} Brand IDs")


def add_indexes():
	"""Add unique indexes on integer ID fields for faster lookups"""
	
	print("Adding database indexes...")
	
	# Add unique index on category_id
	try:
		frappe.db.sql("""
			ALTER TABLE `tabCH Category` 
			ADD UNIQUE INDEX IF NOT EXISTS idx_category_id (category_id)
		""")
		print("✅ Added index on CH Category.category_id")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			print(f"⚠️ Could not add index on CH Category: {e}")
	
	# Add unique index on sub_category_id
	try:
		frappe.db.sql("""
			ALTER TABLE `tabCH Sub Category`
			ADD UNIQUE INDEX IF NOT EXISTS idx_sub_category_id (sub_category_id)
		""")
		print("✅ Added index on CH Sub Category.sub_category_id")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			print(f"⚠️ Could not add index on CH Sub Category: {e}")
	
	# Add unique index on model_id
	try:
		frappe.db.sql("""
			ALTER TABLE `tabCH Model`
			ADD UNIQUE INDEX IF NOT EXISTS idx_model_id (model_id)
		""")
		print("✅ Added index on CH Model.model_id")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			print(f"⚠️ Could not add index on CH Model: {e}")
	
	# Add unique index on channel_id
	try:
		frappe.db.sql("""
			ALTER TABLE `tabCH Price Channel`
			ADD UNIQUE INDEX IF NOT EXISTS idx_channel_id (channel_id)
		""")
		print("✅ Added index on CH Price Channel.channel_id")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			print(f"⚠️ Could not add index on CH Price Channel: {e}")
	
	# Add unique index on warranty_plan_id
	try:
		frappe.db.sql("""
			ALTER TABLE `tabCH Warranty Plan`
			ADD UNIQUE INDEX IF NOT EXISTS idx_warranty_plan_id (warranty_plan_id)
		""")
		print("✅ Added index on CH Warranty Plan.warranty_plan_id")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			print(f"⚠️ Could not add index on CH Warranty Plan: {e}")
	
	# Add unique index on manufacturer_id (Custom Field)
	try:
		frappe.db.sql("""
			ALTER TABLE `tabManufacturer`
			ADD UNIQUE INDEX IF NOT EXISTS idx_manufacturer_id (manufacturer_id)
		""")
		print("✅ Added index on Manufacturer.manufacturer_id")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			print(f"⚠️ Could not add index on Manufacturer: {e}")
	
	# Add unique index on brand_id (Custom Field)
	try:
		frappe.db.sql("""
			ALTER TABLE `tabBrand`
			ADD UNIQUE INDEX IF NOT EXISTS idx_brand_id (brand_id)
		""")
		print("✅ Added index on Brand.brand_id")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			print(f"⚠️ Could not add index on Brand: {e}")
