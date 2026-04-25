"""Product Mapper — AI-assisted resolution of supplier product names to internal items.

This module sits between the AI extraction step and scheme creation.
For each product line extracted from a supplier scheme document, it:
  1. Looks up the Scheme Product Map for existing verified mappings
  2. For unmapped products, calls GPT-4o to suggest the best match
  3. Returns enriched data with item_code / model / item_group populated

Usage:
    from ch_item_master.supplier_scheme.product_mapper import resolve_scheme_products
    result = resolve_scheme_products(brand, extracted_schemes_json)
"""

import json

import frappe
from frappe import _
from frappe.utils import flt


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def resolve_scheme_products(brand, schemes_json) -> dict:
	"""Resolve all product names in extracted scheme data to internal items.

	Args:
		brand: Brand name (e.g. "Samsung", "Oppo")
		schemes_json: JSON string or dict of extracted scheme data with `schemes` key

	Returns:
		dict with:
			- schemes: enriched scheme data with mapping info per rule line
			- stats: {total, resolved, ai_suggested, unresolved}
			- new_mappings: list of AI-suggested mappings for user review
	"""
	if isinstance(schemes_json, str):
		data = json.loads(schemes_json)
	else:
		data = schemes_json

	if not brand:
		frappe.throw(_("Brand is required for product resolution"), title=_("Validation Error"))

	schemes = data.get("schemes", [])
	if not schemes:
		return {"schemes": [], "stats": {"total": 0}, "new_mappings": []}

	# 1. Build the product catalog for this brand (fetched once)
	catalog = _build_brand_catalog(brand)

	# 2. Load existing verified mappings for this brand
	existing_maps = _load_existing_maps(brand)

	# 3. Resolve each product line
	stats = {"total": 0, "resolved": 0, "ai_suggested": 0, "unresolved": 0}
	new_mappings = []
	# Track unique product names to avoid duplicate AI calls
	ai_cache = {}

	for scheme in schemes:
		for rule_line in scheme.get("rules", []):
			stats["total"] += 1
			product_key = _build_product_key(rule_line)

			if not product_key:
				stats["unresolved"] += 1
				rule_line["_mapping_status"] = "no_product_name"
				continue

			# Check existing map
			existing = existing_maps.get(product_key)
			if existing:
				_apply_mapping(rule_line, existing)
				rule_line["_mapping_status"] = "resolved"
				rule_line["_mapping_source"] = existing.get("mapping_source", "Verified")
				rule_line["_map_name"] = existing.get("name")
				stats["resolved"] += 1
				continue

			# Check AI cache (same product already resolved in this batch)
			if product_key in ai_cache:
				cached = ai_cache[product_key]
				_apply_mapping(rule_line, cached)
				rule_line["_mapping_status"] = "ai_suggested"
				rule_line["_mapping_source"] = "AI Suggested"
				stats["ai_suggested"] += 1
				continue

			# No existing mapping — try AI
			if catalog["models"] or catalog["items"]:
				suggestion = _ai_suggest_mapping(brand, product_key, rule_line, catalog)
				if suggestion and suggestion.get("match_level"):
					_apply_mapping(rule_line, suggestion)
					rule_line["_mapping_status"] = "ai_suggested"
					rule_line["_mapping_source"] = "AI Suggested"
					rule_line["_confidence"] = suggestion.get("confidence_score", 0)
					rule_line["_ai_reasoning"] = suggestion.get("ai_reasoning", "")
					ai_cache[product_key] = suggestion
					new_mappings.append({
						"brand": brand,
						"supplier_product_name": product_key,
						"supplier_series": rule_line.get("series", ""),
						"supplier_variant": rule_line.get("model_variant", ""),
						**suggestion,
					})
					stats["ai_suggested"] += 1
					continue

			# Could not resolve
			rule_line["_mapping_status"] = "unresolved"
			stats["unresolved"] += 1

	return {
		"schemes": schemes,
		"stats": stats,
		"new_mappings": new_mappings,
	}


@frappe.whitelist()
def save_mappings(mappings_json, scheme=None) -> dict:
	"""Save confirmed product mappings to the Scheme Product Map table.

	Args:
		mappings_json: JSON array of mapping dicts, each with:
			brand, supplier_product_name, match_level, item_code/model/item_group,
			supplier_series, supplier_variant, mapping_source, confidence_score, ai_reasoning
		scheme: optional Supplier Scheme Circular name to link mappings to
	Returns:
		dict with saved count
	"""
	if isinstance(mappings_json, str):
		mappings = json.loads(mappings_json)
	else:
		mappings = mappings_json

	saved = 0
	for m in mappings:
		if not m.get("brand") or not m.get("supplier_product_name"):
			continue

		# Prefer scheme-scoped duplicate check, fall back to brand-only
		dup_filters = {"brand": m["brand"], "supplier_product_name": m["supplier_product_name"]}
		if scheme:
			dup_filters["scheme"] = scheme

		# Skip if already exists
		if frappe.db.exists("Scheme Product Map", dup_filters):
			# Update existing
			existing_name = frappe.db.get_value("Scheme Product Map", dup_filters, "name")
			doc = frappe.get_doc("Scheme Product Map", existing_name)
			doc.match_level = m.get("match_level", doc.match_level)
			doc.item_code = m.get("item_code", doc.item_code)
			doc.model = m.get("model", doc.model)
			doc.item_group = m.get("item_group", doc.item_group)
			doc.mapping_source = m.get("mapping_source", "AI Verified")
			doc.confidence_score = flt(m.get("confidence_score", doc.confidence_score))
			doc.ai_reasoning = m.get("ai_reasoning", doc.ai_reasoning)
			if scheme and not doc.scheme:
				doc.scheme = scheme
			doc.save(ignore_permissions=True)
			saved += 1
			continue

		doc = frappe.new_doc("Scheme Product Map")
		doc.scheme = scheme or ""
		doc.brand = m["brand"]
		doc.supplier_product_name = m["supplier_product_name"]
		doc.supplier_series = m.get("supplier_series", "")
		doc.supplier_variant = m.get("supplier_variant", "")
		doc.match_level = m.get("match_level", "Model")
		doc.item_code = m.get("item_code", "")
		doc.model = m.get("model", "")
		doc.item_group = m.get("item_group", "")
		doc.mapping_source = m.get("mapping_source", "AI Suggested")
		doc.confidence_score = flt(m.get("confidence_score", 0))
		doc.ai_reasoning = m.get("ai_reasoning", "")
		doc.insert(ignore_permissions=True)
		saved += 1

	return {"saved": saved}


@frappe.whitelist()
def get_mapping_coverage(brand) -> dict:
	"""Get mapping coverage stats for a brand.

	Returns how many unique product names from recent schemes
	have verified mappings vs unmapped.
	"""
	if not brand:
		return {}

	total_maps = frappe.db.count("Scheme Product Map", {"brand": brand})
	verified = frappe.db.count("Scheme Product Map", {
		"brand": brand,
		"mapping_source": ["in", ["Verified", "AI Verified"]],
	})
	suggested = frappe.db.count("Scheme Product Map", {
		"brand": brand,
		"mapping_source": "AI Suggested",
	})

	return {
		"total": total_maps,
		"verified": verified,
		"suggested": suggested,
		"unverified_pct": round((suggested / total_maps * 100) if total_maps else 0, 1),
	}


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _build_product_key(rule_line):
	"""Build a canonical product name from series + variant."""
	series = (rule_line.get("series") or "").strip()
	variant = (rule_line.get("model_variant") or "").strip()
	if series and variant:
		return f"{series} {variant}"
	return series or variant or ""


def _load_existing_maps(brand):
	"""Load all Scheme Product Map entries for a brand into a dict keyed by supplier_product_name."""
	maps = frappe.get_all(
		"Scheme Product Map",
		filters={"brand": brand},
		fields=[
			"name", "supplier_product_name", "match_level",
			"item_code", "model", "item_group",
			"mapping_source", "confidence_score",
		],
	)
	return {m["supplier_product_name"]: m for m in maps}


def _build_brand_catalog(brand):
	"""Fetch all CH Models and Items for a brand — used as AI context."""
	models = frappe.get_all(
		"CH Model",
		filters={"brand": brand, "disabled": 0},
		fields=["name", "model_name", "sub_category"],
		limit_page_length=0,
	)

	items = frappe.get_all(
		"Item",
		filters={"brand": brand, "disabled": 0},
		fields=["name", "item_name", "ch_model", "item_group", "ch_display_name"],
		limit_page_length=500,
	)

	item_groups = list({i["item_group"] for i in items if i.get("item_group")})

	return {
		"models": models,
		"items": items,
		"item_groups": item_groups,
	}


def _apply_mapping(rule_line, mapping):
	"""Apply a mapping dict onto a rule_line, setting item_code/model/item_group."""
	level = mapping.get("match_level", "")
	if level == "Item":
		rule_line["_item_code"] = mapping.get("item_code", "")
	elif level == "Model":
		rule_line["_model"] = mapping.get("model", "")
	elif level == "Item Group":
		rule_line["_item_group"] = mapping.get("item_group", "")
	rule_line["_match_level"] = level


def _ai_suggest_mapping(brand, product_key, rule_line, catalog):
	"""Call AI to suggest the best mapping for a product name.

	Returns a dict with match_level, item_code/model/item_group,
	confidence_score, ai_reasoning. Or None if AI is unavailable.
	"""
	ai_settings = _get_ai_settings()
	if not ai_settings:
		return None

	# Build compact model list for context
	model_list = []
	for m in catalog["models"]:
		model_list.append(f"- {m['name']} (Model Name: {m.get('model_name', '')}, Category: {m.get('sub_category', '')})")

	# Build compact item sample (group by model to reduce tokens)
	model_items = {}
	for item in catalog["items"]:
		model_key = item.get("ch_model") or "No Model"
		if model_key not in model_items:
			model_items[model_key] = []
		if len(model_items[model_key]) < 3:  # max 3 items per model
			display = item.get("ch_display_name") or item.get("item_name") or item["name"]
			model_items[model_key].append(f"{item['name']} ({display})")

	items_text = ""
	for model_key, item_list in model_items.items():
		items_text += f"\n  Model '{model_key}': {', '.join(item_list)}"

	prompt = f"""You are matching a supplier scheme product name to our internal item catalog.

SUPPLIER PRODUCT: "{product_key}"
  Series from scheme: "{rule_line.get('series', '')}"
  Variant from scheme: "{rule_line.get('model_variant', '')}"
BRAND: {brand}

OUR MODELS (CH Model):
{chr(10).join(model_list) if model_list else "  (none)"}

OUR ITEMS (sample, grouped by model):
{items_text if items_text else "  (none)"}

OUR ITEM GROUPS: {', '.join(catalog['item_groups'][:30]) if catalog['item_groups'] else '(none)'}

TASK: Find the best match in our catalog.

Rules:
1. If the product name clearly maps to a SPECIFIC item (with exact variant like color+storage), return match_level="Item" with the item_code.
2. If the product name maps to a model (e.g. "Galaxy S24 Ultra 256GB" but no color specified), return match_level="Model" with our CH Model name.
3. If the product is too broad (e.g. "All Galaxy S24 Series"), return match_level="Item Group" with the matching item group.
4. If nothing matches, return match_level="" with confidence=0.

Consider:
- Variant info like "8+128" means 8GB RAM + 128GB storage
- Supplier names often abbreviate (e.g. "A16" = model "Galaxy A16")
- Match storage capacity (128GB, 256GB) as a key differentiator

Return ONLY valid JSON:
{{
  "match_level": "Item" | "Model" | "Item Group" | "",
  "item_code": "exact item code from our list or empty",
  "model": "exact CH Model name from our list or empty",
  "item_group": "exact Item Group name from our list or empty",
  "confidence_score": 0-100,
  "ai_reasoning": "brief explanation"
}}"""

	try:
		import requests

		resp = requests.post(
			ai_settings["endpoint"],
			headers={
				"Authorization": f"Bearer {ai_settings['api_key']}",
				"Content-Type": "application/json",
			},
			json={
				"model": ai_settings["model"],
				"messages": [
					{"role": "system", "content": "You are an expert at matching consumer electronics product names across different naming conventions. Return only valid JSON."},
					{"role": "user", "content": prompt},
				],
				"max_tokens": 500,
				"temperature": 0.1,
				"response_format": {"type": "json_object"},
			},
			timeout=ai_settings["timeout"],
		)
		resp.raise_for_status()

		content = resp.json()["choices"][0]["message"]["content"]
		result = json.loads(content)

		# Validate the AI response — ensure referenced items/models actually exist
		match_level = result.get("match_level", "")
		if match_level == "Item":
			if not result.get("item_code") or not frappe.db.exists("Item", result["item_code"]):
				result["match_level"] = ""
				result["confidence_score"] = 0
		elif match_level == "Model":
			if not result.get("model") or not frappe.db.exists("CH Model", result["model"]):
				result["match_level"] = ""
				result["confidence_score"] = 0
		elif match_level == "Item Group":
			if not result.get("item_group") or not frappe.db.exists("Item Group", result["item_group"]):
				result["match_level"] = ""
				result["confidence_score"] = 0

		return result

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Scheme Product Mapper AI Error")
		return None


def _get_ai_settings():
	"""Get AI config from POS AI Settings."""
	try:
		settings = frappe.get_cached_doc("POS AI Settings")
		if not settings.enable_ai:
			return None
		api_key = settings.get_password("api_key")
		if not api_key:
			return None
		return {
			"api_key": api_key,
			"endpoint": settings.api_endpoint or "https://api.openai.com/v1/chat/completions",
			"model": settings.comparison_model or "gpt-4o",
			"timeout": max(settings.timeout_sec or 30, 30),
		}
	except frappe.DoesNotExistError:
		return None
