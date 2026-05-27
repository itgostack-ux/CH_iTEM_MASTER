import frappe
from frappe.utils import flt


def _item_price_for_pos(item_code: str) -> float:
	price = frappe.db.get_value(
		"CH Item Price",
		{"item_code": item_code, "channel": "POS", "status": "Active"},
		"selling_price",
	)
	return flt(price)


@frappe.whitelist()
def get_vas_product_catalog(limit=50):
	limit = min(int(limit or 50), 200)
	if not frappe.db.exists("DocType", "VAS Product"):
		return []
	return frappe.get_all(
		"VAS Product",
		filters={"is_active": 1},
		fields=["name", "product_name", "service_item", "product_type", "brand", "category"],
		order_by="modified desc",
		limit=limit,
	)


@frappe.whitelist()
def get_vas_plan_catalog(limit=100):
	"""Phase-3 catalog API while still reusing CH Warranty Plan as source of truth."""
	limit = min(int(limit or 100), 300)
	if frappe.db.exists("DocType", "VAS Plan"):
		return frappe.get_all(
			"VAS Plan",
			filters={"status": "Active"},
			fields=["name", "plan_name", "vas_product", "source_warranty_plan", "list_price", "duration_months", "partner"],
			order_by="modified desc",
			limit=limit,
		)

	return frappe.get_all(
		"CH Warranty Plan",
		filters={"status": "Active"},
		fields=["name", "plan_name", "service_item", "price", "duration_months", "plan_type"],
		order_by="modified desc",
		limit=limit,
	)


@frappe.whitelist()
def get_vas_claims(limit=100):
	limit = min(int(limit or 100), 300)
	if frappe.db.exists("DocType", "VAS Claim"):
		return frappe.get_all(
			"VAS Claim",
			fields=["name", "claim_date", "customer", "vas_plan", "claim_status", "approved_amount", "source_warranty_claim"],
			order_by="modified desc",
			limit=limit,
		)

	return frappe.get_all(
		"CH Warranty Claim",
		fields=["name", "claim_date", "customer", "sold_plan", "claim_status", "approved_amount"],
		order_by="modified desc",
		limit=limit,
	)


@frappe.whitelist()
def get_vas_attach_offers(item_code: str, selling_price=None, company=None):
	"""Auto-attach rules by item/category/brand + price band for POS use."""
	if not item_code:
		return []
	if not frappe.db.exists("DocType", "VAS Attach Rule"):
		return []

	item = frappe.get_cached_doc("Item", item_code)
	price = flt(selling_price) if selling_price is not None else _item_price_for_pos(item_code)

	rules = frappe.get_all(
		"VAS Attach Rule",
		filters={"is_active": 1},
		fields=[
			"name", "rule_name", "company", "vas_plan", "item_code", "brand", "category", "sub_category",
			"min_price", "max_price", "auto_add", "mandatory_offer", "priority",
		],
		order_by="priority desc, modified desc",
	)

	out = []
	for rule in rules:
		if company and rule.company and rule.company != company:
			continue
		if rule.item_code and rule.item_code != item_code:
			continue
		if rule.brand and rule.brand != getattr(item, "brand", None):
			continue
		if rule.category and rule.category != getattr(item, "ch_category", None):
			continue
		if rule.sub_category and rule.sub_category != getattr(item, "ch_sub_category", None):
			continue
		if rule.min_price and price < flt(rule.min_price):
			continue
		if rule.max_price and price > flt(rule.max_price):
			continue

		plan = frappe.db.get_value(
			"VAS Plan",
			rule.vas_plan,
			["plan_name", "source_warranty_plan", "list_price", "vas_product"],
			as_dict=True,
		)
		service_item = None
		if plan and plan.get("vas_product"):
			service_item = frappe.db.get_value("VAS Product", plan["vas_product"], "service_item")
		if not service_item and plan and plan.get("source_warranty_plan"):
			service_item = frappe.db.get_value("CH Warranty Plan", plan["source_warranty_plan"], "service_item")

		out.append(
			{
				"name": rule.name,
				"rule_name": rule.rule_name,
				"attach_type": "VAS",
				"mandatory_offer": rule.mandatory_offer,
				"auto_add": rule.auto_add,
				"priority": rule.priority,
				"vas_plan": rule.vas_plan,
				"plan_name": plan.get("plan_name") if plan else None,
				"source_warranty_plan": plan.get("source_warranty_plan") if plan else None,
				"attach_items": [
					{
						"item_code": service_item,
						"item_name": frappe.db.get_value("Item", service_item, "item_name") if service_item else None,
						"is_mandatory_offer": 1 if rule.mandatory_offer else 0,
					}
				],
			}
		)

	return out


@frappe.whitelist()
def auto_match_partner_commissions(partner=None):
	"""Simple reconciliation helper: mark rows with settlement_reference as matched."""
	if not frappe.db.exists("DocType", "VAS Commission"):
		return {"updated": 0}

	filters = {"settlement_status": "Pending"}
	if partner:
		filters["partner"] = partner

	rows = frappe.get_all(
		"VAS Commission",
		filters=filters,
		fields=["name", "settlement_reference"],
		limit=5000,
	)
	updated = 0
	for row in rows:
		if row.settlement_reference:
			frappe.db.set_value(
				"VAS Commission",
				row.name,
				"settlement_status",
				"Matched",
				update_modified=False,
			)
			updated += 1

	return {"updated": updated}
