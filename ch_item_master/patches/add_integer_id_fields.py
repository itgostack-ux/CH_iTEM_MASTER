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
