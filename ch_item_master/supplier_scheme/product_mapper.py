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

from ch_item_master.config import get_int_setting, is_privileged_user, require_role_setting
from ch_item_master.outbound_security import parse_exact_host_allowlist, post_json_with_credentials
from ch_item_master.security import ensure_company_access


def _mapping_row_limit() -> int:
	return get_int_setting("bulk_imei_limit", 200, minimum=1)


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def resolve_scheme_products(brand, schemes_json, company=None) -> dict:
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
	require_role_setting(
		"supplier_scheme_approval_roles",
		("Purchase Manager", "Scheme Manager"),
		action=_("resolve supplier product mappings"),
	)
	if not company and not is_privileged_user():
		frappe.throw(_("Company is required for product resolution."), frappe.PermissionError)
	if company:
		ensure_company_access(company)
		frappe.get_doc("Company", company).check_permission("read")
	if isinstance(schemes_json, str):
		data = json.loads(schemes_json)
	else:
		data = schemes_json
	if not isinstance(data, dict):
		frappe.throw(_("Scheme data must be a JSON object."), frappe.ValidationError)

	if not brand:
		frappe.throw(_("Brand is required for product resolution"), title=_("Validation Error"))

	schemes = data.get("schemes", [])
	if not schemes:
		return {"schemes": [], "stats": {"total": 0}, "new_mappings": []}
	if not isinstance(schemes, list):
		frappe.throw(_("Schemes must be a JSON list."), frappe.ValidationError)

	row_limit = _mapping_row_limit()
	product_keys = set()
	rule_count = 0
	for scheme in schemes:
		if not isinstance(scheme, dict) or not isinstance(scheme.get("rules", []), list):
			frappe.throw(_("Every scheme must contain a rules list."), frappe.ValidationError)
		rules = scheme.get("rules", [])
		rule_count += len(rules)
		if rule_count > row_limit:
			frappe.throw(
				_("A maximum of {0} scheme rules can be resolved at once.").format(row_limit),
				frappe.ValidationError,
			)
		for rule_line in rules:
			if not isinstance(rule_line, dict):
				frappe.throw(_("Every scheme rule must be a JSON object."), frappe.ValidationError)
			product_key = _build_product_key(rule_line)
			if product_key:
				product_keys.add(product_key)

	existing_maps = _load_existing_maps(brand, product_keys, company=company)
	unmapped_keys = product_keys.difference(existing_maps)
	catalog = _build_brand_catalog(brand) if unmapped_keys else {"models": [], "items": [], "item_groups": []}

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


@frappe.whitelist(methods=["POST"])
def save_mappings(mappings_json, scheme=None, company=None) -> dict:
	"""Save confirmed product mappings to the Scheme Product Map table.

	Args:
		mappings_json: JSON array of mapping dicts, each with:
			brand, supplier_product_name, match_level, item_code/model/item_group,
			supplier_series, supplier_variant, mapping_source, confidence_score, ai_reasoning
		scheme: optional Supplier Scheme Circular name to link mappings to
	Returns:
		dict with saved count
	"""
	require_role_setting(
		"supplier_scheme_management_roles",
		("Accounts Manager", "Purchase Manager", "Scheme Manager"),
		action=_("save supplier product mappings"),
	)
	if isinstance(mappings_json, str):
		mappings = json.loads(mappings_json)
	else:
		mappings = mappings_json
	if not isinstance(mappings, list):
		frappe.throw(_("Mappings must be a JSON list."), frappe.ValidationError)
	row_limit = _mapping_row_limit()
	if len(mappings) > row_limit:
		frappe.throw(
			_("A maximum of {0} mappings can be saved at once.").format(row_limit),
			frappe.ValidationError,
		)
	if scheme:
		scheme_doc = frappe.get_doc("Supplier Scheme Circular", scheme)
		scheme_doc.check_permission("read")
		if company and company != scheme_doc.company:
			frappe.throw(_("Product map company must match the supplier scheme."), frappe.ValidationError)
		company = scheme_doc.company
	if not company and not is_privileged_user():
		frappe.throw(_("Company is required for product mappings."), frappe.PermissionError)
	if company:
		ensure_company_access(company)
		frappe.get_doc("Company", company).check_permission("read")

	valid_mappings = [
		mapping
		for mapping in mappings
		if isinstance(mapping, dict)
		and mapping.get("brand")
		and mapping.get("supplier_product_name")
	]
	existing_rows = []
	if valid_mappings:
		existing_filters = {
			"company": company if company else ("is", "not set"),
			"brand": ("in", sorted({mapping["brand"] for mapping in valid_mappings})),
			"supplier_product_name": (
				"in",
				sorted({mapping["supplier_product_name"] for mapping in valid_mappings}),
			),
		}
		if scheme:
			existing_filters["scheme"] = scheme
		existing_rows = frappe.get_all(
			"Scheme Product Map",
			filters=existing_filters,
			fields=["name", "company", "brand", "supplier_product_name", "scheme"],
			order_by="modified desc",
			limit_page_length=max(row_limit * 5, row_limit),
		)
	existing_by_key = {}
	for row in existing_rows:
		key = (row.brand, row.supplier_product_name, row.scheme) if scheme else (
			row.brand,
			row.supplier_product_name,
		)
		existing_by_key.setdefault(key, row.name)

	saved = 0
	for m in mappings:
		if not isinstance(m, dict) or not m.get("brand") or not m.get("supplier_product_name"):
			continue

		key = (m["brand"], m["supplier_product_name"], scheme) if scheme else (
			m["brand"],
			m["supplier_product_name"],
		)
		existing_name = existing_by_key.get(key)
		if existing_name:
			doc = frappe.get_doc("Scheme Product Map", existing_name)
			doc.check_permission("write")
			if (doc.company or None) != (company or None):
				frappe.throw(_("Product map belongs to another company."), frappe.PermissionError)
			doc.match_level = m.get("match_level", doc.match_level)
			doc.item_code = m.get("item_code", doc.item_code)
			doc.model = m.get("model", doc.model)
			doc.item_group = m.get("item_group", doc.item_group)
			doc.mapping_source = m.get("mapping_source", "AI Verified")
			doc.confidence_score = flt(m.get("confidence_score", doc.confidence_score))
			doc.ai_reasoning = m.get("ai_reasoning", doc.ai_reasoning)
			if scheme and not doc.scheme:
				doc.scheme = scheme
			doc.save()
			saved += 1
			continue

		doc = frappe.new_doc("Scheme Product Map")
		doc.company = company
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
		frappe.has_permission("Scheme Product Map", ptype="create", throw=True)
		doc.insert()
		existing_by_key[key] = doc.name
		saved += 1

	return {"saved": saved}


@frappe.whitelist()
def get_mapping_coverage(brand, company=None) -> dict:
	"""Get mapping coverage stats for a brand.

	Returns how many unique product names from recent schemes
	have verified mappings vs unmapped.
	"""
	if not brand:
		return {}
	require_role_setting(
		"supplier_scheme_management_roles",
		("Accounts Manager", "Purchase Manager", "Scheme Manager"),
		action=_("view supplier product mapping coverage"),
	)
	if not company and not is_privileged_user():
		frappe.throw(_("Company is required for mapping coverage."), frappe.PermissionError)
	if company:
		ensure_company_access(company)

	company_condition = "= %(company)s" if company else "IS NULL"
	row = frappe.db.sql(
		f"""
		SELECT
			COUNT(*) AS total,
			SUM(mapping_source IN ('Verified', 'AI Verified')) AS verified,
			SUM(mapping_source = 'AI Suggested') AS suggested
		FROM `tabScheme Product Map`
		WHERE brand = %(brand)s
			AND company {company_condition}
		""",
		{"brand": brand, "company": company},
		as_dict=True,
	)[0]
	total_maps = int(row.total or 0)
	verified = int(row.verified or 0)
	suggested = int(row.suggested or 0)

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


def _load_existing_maps(brand, product_keys=None, company=None):
	"""Load all Scheme Product Map entries for a brand into a dict keyed by supplier_product_name."""
	if product_keys is not None and not product_keys:
		return {}
	filters = {
		"brand": brand,
		"company": company if company else ("is", "not set"),
	}
	if product_keys:
		filters["supplier_product_name"] = ("in", sorted(product_keys))
	maps = frappe.get_all(
		"Scheme Product Map",
		filters=filters,
		fields=[
			"name", "supplier_product_name", "match_level",
			"item_code", "model", "item_group",
			"mapping_source", "confidence_score",
		],
		order_by="modified asc",
		limit_page_length=max(_mapping_row_limit() * 5, _mapping_row_limit()),
	)
	return {mapping["supplier_product_name"]: mapping for mapping in maps}


def _build_brand_catalog(brand):
	"""Fetch all CH Models and Items for a brand — used as AI context."""
	catalog_limit = max(_mapping_row_limit(), 500)
	models = frappe.get_all(
		"CH Model",
		filters={"brand": brand, "disabled": 0},
		fields=["name", "model_name", "sub_category"],
		order_by="modified desc",
		limit_page_length=catalog_limit,
	)

	items = frappe.get_all(
		"Item",
		filters={"brand": brand, "disabled": 0},
		fields=["name", "item_name", "ch_model", "item_group", "ch_display_name"],
		order_by="modified desc",
		limit_page_length=catalog_limit,
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
		endpoint = ai_settings["endpoint"]
		response = post_json_with_credentials(
			endpoint,
			allowed_hosts=parse_exact_host_allowlist(
				ai_settings["allowed_hosts"],
				label="Supplier Scheme AI",
			),
			label="Supplier Scheme AI",
			headers={
				"Authorization": f"Bearer {ai_settings['api_key']}",
				"Content-Type": "application/json",
			},
			payload={
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
			max_response_bytes=ai_settings["max_response_bytes"],
		)

		content = response["choices"][0]["message"]["content"]
		result = json.loads(content)

		# Validate the AI response — ensure referenced items/models actually exist
		match_level = result.get("match_level", "")
		if match_level == "Item":
			valid_items = {item["name"] for item in catalog["items"]}
			if not result.get("item_code") or result["item_code"] not in valid_items:
				result["match_level"] = ""
				result["confidence_score"] = 0
		elif match_level == "Model":
			valid_models = {model["name"] for model in catalog["models"]}
			if not result.get("model") or result["model"] not in valid_models:
				result["match_level"] = ""
				result["confidence_score"] = 0
		elif match_level == "Item Group":
			if not result.get("item_group") or result["item_group"] not in catalog["item_groups"]:
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
			"allowed_hosts": settings.get("allowed_api_hosts") or "api.openai.com",
			"model": settings.comparison_model or "gpt-4o",
			"timeout": max(settings.timeout_sec or 30, 30),
			"max_response_bytes": min(
				get_int_setting("supplier_scheme_ai_response_byte_limit", 1048576, minimum=1024),
				8388608,
			),
		}
	except frappe.DoesNotExistError:
		return None
